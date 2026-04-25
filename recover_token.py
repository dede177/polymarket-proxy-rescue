#!/usr/bin/env python3
"""
Diagnose and recover ERC-20 tokens sent to a Polymarket proxy wallet.

Safety model:
- EOA and proxy addresses are read from a JSON wallet file whose format matches
  `polymarket wallet show --output json` (fields: `address`, `proxy_address`,
  optional `config_path`).
- Private key is loaded from the wallet file's `config_path` (a polymarket CLI
  config containing `private_key`) when present; otherwise it is prompted via
  getpass. It is never accepted as a CLI arg or environment variable.
- Proxy-held Native USDC is recovered to the EOA from the wallet file.
- The script simulates and estimates gas, then prompts for confirmation before broadcasting.

Dependencies:
    python -m pip install web3 eth-account

Run:
    python recover_token.py --wallet-file wallet.json

You can produce the wallet file with:
    polymarket wallet show --output json > wallet.json

Defaults:
- token checked: Polygon Native USDC
- RPC: POLYMARKET_RPC_URL or the built-in Polygon RPC default

The recovery transaction calls the Polymarket proxy factory. If the proxy has not
been deployed yet, the factory deploys it and transfers the proxy's token balance
to the destination in the same transaction.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal, getcontext
from getpass import getpass
from typing import Any

Account = None
Web3 = None
keccak = None
to_bytes = None
to_checksum_address = None


def import_deps() -> None:
    global Account, Web3, keccak, to_bytes, to_checksum_address
    try:
        from eth_account import Account as _Account
        from eth_utils import keccak as _keccak
        from eth_utils import to_bytes as _to_bytes
        from eth_utils import to_checksum_address as _to_checksum_address
        from web3 import Web3 as _Web3
    except ImportError as exc:  # pragma: no cover - user environment dependent
        print(
            "Missing dependency. Install with:\n"
            "  python -m pip install web3 eth-account\n",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc

    Account = _Account
    Web3 = _Web3
    keccak = _keccak
    to_bytes = _to_bytes
    to_checksum_address = _to_checksum_address


CHAIN_ID = 137
CHAIN_NAME = "Polygon mainnet"
NATIVE_GAS_SYMBOL = "POL"
DEFAULT_RPC_URL = "https://polygon.drpc.org"
USDC_E_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # PoS-bridged USDC.e (used by Polymarket)
NATIVE_USDC_ADDRESS = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"  # Circle-native USDC on Polygon
DEFAULT_TOKENS = [USDC_E_ADDRESS, NATIVE_USDC_ADDRESS]

# Must match polymarket-client-sdk 0.4.x derivation used by this CLI.
PROXY_FACTORY = "0xaB45c5A4B0c941a2F231C04C3f49182e1A254052"
PROXY_INIT_CODE_HASH = "0xd21df8dc65880a8606f09fe0ce3df9b8869287ab0b058be05aa9e8af6330a00b"

# Minimal ERC-20 ABI for balance scanning and transfer recovery.
ERC20_ABI: list[dict[str, Any]] = [
    {
        "constant": True,
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "value", "type": "uint256"},
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "payable": False,
        "stateMutability": "nonpayable",
        "type": "function",
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Polymarket proxy Native USDC recovery wizard."
    )
    parser.add_argument(
        "--wallet-file",
        required=True,
        help=(
            "Path to a JSON wallet file matching `polymarket wallet show --output json`. "
            "Required fields: `address`, `proxy_address`. Optional: `config_path` (a "
            "polymarket CLI config file containing `private_key`)."
        ),
    )
    parser.add_argument(
        "--token",
        action="append",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--rpc", help=argparse.SUPPRESS)
    return parser.parse_args()


def banner() -> None:
    print()
    print("Polymarket ERC-20 Recovery Wizard")
    print("────────────────────────────────")
    print("Inputs: --wallet-file (JSON, format of `polymarket wallet show --output json`)")
    print("        and a private key (read from the wallet file's config_path or prompted).")
    print("Default tokens checked: Polygon USDC.e (bridged) and native USDC.")
    print("Proxy-held tokens are recovered to the EOA from the wallet file in a single batched tx.")
    print()


def checksum_address(raw: str, label: str) -> str:
    try:
        return to_checksum_address(raw)
    except ValueError as exc:
        raise SystemExit(f"Invalid {label} address: {raw}") from exc


def load_wallet_file(path: str) -> tuple[str, str, str | None]:
    """Read EOA, proxy, and optional config_path from a `polymarket wallet show` JSON file."""
    try:
        with open(path) as fh:
            data = json.load(fh)
    except OSError as exc:
        raise SystemExit(f"Could not read wallet file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Wallet file {path} is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise SystemExit(f"Wallet file {path} must be a JSON object")

    eoa_raw = data.get("address")
    proxy_raw = data.get("proxy_address")
    if not eoa_raw:
        raise SystemExit(f"Wallet file {path} missing required field `address`")
    if not proxy_raw:
        raise SystemExit(f"Wallet file {path} missing required field `proxy_address`")

    eoa = checksum_address(eoa_raw, "wallet file `address`")
    proxy = checksum_address(proxy_raw, "wallet file `proxy_address`")
    config_path = data.get("config_path")
    if config_path is not None and not isinstance(config_path, str):
        raise SystemExit(f"Wallet file {path} has non-string `config_path`")
    return eoa, proxy, config_path or None


def load_private_key_from_config(config_path: str) -> str | None:
    """Return the `private_key` field from a polymarket CLI config file, or None if absent."""
    try:
        with open(config_path) as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SystemExit(f"Could not read config file {config_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Config file {config_path} is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise SystemExit(f"Config file {config_path} must be a JSON object")

    key = data.get("private_key")
    if key is None:
        return None
    if not isinstance(key, str) or not key.strip():
        raise SystemExit(f"Config file {config_path} has invalid `private_key`")
    return key.strip()


def derive_proxy_wallet(eoa_address: str) -> str:
    """Derive the same Polygon proxy address as polymarket_client_sdk::derive_proxy_wallet."""
    factory_bytes = to_bytes(hexstr=PROXY_FACTORY)
    salt = keccak(to_bytes(hexstr=eoa_address))
    init_code_hash = to_bytes(hexstr=PROXY_INIT_CODE_HASH)
    digest = keccak(b"\xff" + factory_bytes + salt + init_code_hash)
    return to_checksum_address(digest[-20:])


def require_chain(w3: Web3) -> None:
    try:
        chain_id = w3.eth.chain_id
    except Exception as exc:  # noqa: BLE001 - display RPC failure clearly
        raise SystemExit(f"Could not connect to RPC: {exc}") from exc
    if chain_id != CHAIN_ID:
        raise SystemExit(f"RPC chain id is {chain_id}, expected {CHAIN_NAME} {CHAIN_ID}")


def call_or_default(fn: Any, default: Any) -> Any:
    try:
        return fn().call()
    except Exception:  # noqa: BLE001 - optional token metadata may be non-standard
        return default


def human_amount(raw: int, decimals: int) -> str:
    getcontext().prec = 80
    scale = Decimal(10) ** decimals
    value = Decimal(raw) / scale
    return format(value.normalize(), "f") if value != 0 else "0"


def word(value: int) -> str:
    return hex(value)[2:].rjust(64, "0")


def address_word(address: str) -> str:
    return address.lower().removeprefix("0x").rjust(64, "0")


def encode_bytes(hex_data: str) -> str:
    data = hex_data.removeprefix("0x")
    length = len(data) // 2
    padding = "00" * ((32 - (length % 32)) % 32)
    return word(length) + data + padding


def erc20_transfer_calldata(to_addr: str, raw_amount: int) -> str:
    # transfer(address,uint256)
    return "a9059cbb" + address_word(to_addr) + word(raw_amount)


def proxy_recovery_calldata(to_addr: str, transfers: list[tuple[str, int]]) -> str:
    """Encode the reverse-engineered Polymarket proxy factory batch call.

    Selector 0x34ee9791 accepts an array of call actions. Action operation 1 is CALL:
      (operation=1, target=token, value=0, data=ERC20.transfer(to, amount))

    Each entry in `transfers` is (token_addr, raw_amount). All transfers go to
    the same `to_addr`. When sent to the proxy factory from the EOA, the factory
    resolves/deploys that EOA's deterministic proxy and executes each action.
    """
    n = len(transfers)
    if n == 0:
        raise ValueError("at least one transfer is required")

    # ABI encoding for one argument:
    #   tuple(uint256 operation, address target, uint256 value, bytes data)[]
    # Each tuple is dynamic (contains bytes), so the array head holds N offsets
    # into the tail. Each tuple is 256 bytes: 4-word head + 128-byte encoded transfer.
    head_size = 32 * n
    tuple_size = 256

    encoded = word(32) + word(n)
    for i in range(n):
        encoded += word(head_size + tuple_size * i)

    for token_addr, raw_amount in transfers:
        transfer = erc20_transfer_calldata(to_addr, raw_amount)
        encoded += (
            word(1)                      # operation: CALL
            + address_word(token_addr)
            + word(0)                    # native value
            + word(128)                  # offset to bytes data inside tuple
            + encode_bytes(transfer)
        )

    return "0x34ee9791" + encoded


def build_proxy_recovery_tx(
    w3: Web3,
    from_addr: str,
    to_addr: str,
    transfers: list[tuple[str, int]],
) -> dict[str, Any]:
    data = proxy_recovery_calldata(to_addr, transfers)

    tx = {
        "from": from_addr,
        "to": checksum_address(PROXY_FACTORY, "proxy factory"),
        "data": data,
        "nonce": w3.eth.get_transaction_count(from_addr),
        "chainId": CHAIN_ID,
        "value": 0,
    }

    try:
        # Simulate first. For ERC-20 transfer this should return bytes[] with bool true.
        w3.eth.call(tx)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"Recovery simulation failed; transaction may revert: {exc}") from exc

    try:
        tx["gas"] = int(w3.eth.estimate_gas(tx))
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"Gas estimation failed; transaction may revert: {exc}") from exc

    tx["gasPrice"] = int(w3.eth.gas_price)
    return tx


def print_summary(
    eoa: str,
    proxy: str,
    proxy_deployed: bool,
    native_balance_wei: int,
    token_infos: list[dict[str, Any]],
) -> None:
    print("\nWallets")
    print(f"  EOA:          {eoa}")
    print(f"  Proxy:        {proxy}")
    print(f"  Proxy code:   {'deployed' if proxy_deployed else 'not deployed'}")
    print(f"  EOA {NATIVE_GAS_SYMBOL}:       {Web3.from_wei(native_balance_wei, 'ether')} {NATIVE_GAS_SYMBOL}")

    print("\nTokens")
    for info in token_infos:
        symbol = info["symbol"]
        decimals = info["decimals"]
        print(f"  {symbol} ({info['address']})")
        print(f"    EOA balance:    {human_amount(info['eoa_balance'], decimals)} {symbol}")
        print(f"    Proxy balance:  {human_amount(info['proxy_balance'], decimals)} {symbol}")


def main() -> int:
    args = parse_args()
    import_deps()

    banner()

    print("Step 1/3: Load wallet file")
    eoa, proxy, config_path = load_wallet_file(args.wallet_file)
    print(f"  Wallet file: {args.wallet_file}")
    print(f"  EOA:   {eoa}")
    print(f"  Proxy: {proxy}")

    derived_proxy = derive_proxy_wallet(eoa)
    if derived_proxy != proxy:
        raise SystemExit(
            f"Wallet file `proxy_address` {proxy} does not match the proxy derived "
            f"from `address`: expected {derived_proxy}. Refusing to continue."
        )

    print("\nStep 2/3: Load private key")
    private_key: str | None = None
    if config_path:
        private_key = load_private_key_from_config(config_path)
        if private_key:
            print(f"  ✓ Private key loaded from config_path: {config_path}")
        else:
            print(f"  config_path {config_path} has no `private_key`; falling back to prompt.")

    if not private_key:
        print("  The key is hidden as you type and is not saved.")
        private_key = getpass("  Enter private key: ").strip()
        if not private_key:
            raise SystemExit("Private key is required")

    if not private_key.startswith("0x"):
        private_key = "0x" + private_key

    try:
        account = Account.from_key(private_key)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"Invalid private key: {exc}") from exc

    derived_eoa = to_checksum_address(account.address)
    if derived_eoa != eoa:
        raise SystemExit(
            f"Private key controls EOA {derived_eoa}, but wallet file specifies {eoa}. "
            "Refusing to continue."
        )
    print("  ✓ Private key matches wallet file EOA")

    print("\nStep 3/3: Connect, scan, and recover ERC-20 balances")
    rpc_url = args.rpc or os.environ.get("POLYMARKET_RPC_URL", DEFAULT_RPC_URL)
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    require_chain(w3)
    print(f"  ✓ Connected to {CHAIN_NAME}")

    token_addrs_raw = args.token if args.token else DEFAULT_TOKENS
    token_addrs = [checksum_address(a, "token") for a in token_addrs_raw]
    if args.token:
        print(f"  Token override: {', '.join(token_addrs)}")
    else:
        print(f"  Tokens: USDC.e + native USDC ({len(token_addrs)} contracts)")

    proxy_deployed = len(w3.eth.get_code(proxy)) > 0
    native_balance = int(w3.eth.get_balance(eoa))

    token_infos: list[dict[str, Any]] = []
    for addr in token_addrs:
        contract = w3.eth.contract(address=addr, abi=ERC20_ABI)
        decimals = int(call_or_default(contract.functions.decimals, 18))
        symbol = str(call_or_default(contract.functions.symbol, "TOKEN"))
        eoa_balance = int(contract.functions.balanceOf(eoa).call())
        proxy_balance = int(contract.functions.balanceOf(proxy).call())
        token_infos.append({
            "address": addr,
            "symbol": symbol,
            "decimals": decimals,
            "eoa_balance": eoa_balance,
            "proxy_balance": proxy_balance,
        })

    print_summary(eoa, proxy, proxy_deployed, native_balance, token_infos)

    recoverable = [t for t in token_infos if t["proxy_balance"] > 0]
    if not recoverable:
        print("\nNo proxy token balance to recover.")
        if any(t["eoa_balance"] > 0 for t in token_infos):
            print("EOA already holds token balance controlled by this private key.")
        return 0

    print(f"\nProxy holds recoverable balance for {len(recoverable)} token(s)")
    if not proxy_deployed:
        print("  Proxy code is not deployed yet; the recovery transaction will deploy it first.")

    destination = eoa
    print(f"  Recovery destination: {destination} (EOA from wallet file)")

    transfers: list[tuple[str, int]] = []
    print("\nProxy recovery transaction")
    for info in recoverable:
        amount = info["proxy_balance"]
        pretty = human_amount(amount, info["decimals"])
        print(f"  {pretty} {info['symbol']} from {info['address']}")
        transfers.append((info["address"], amount))
    print(f"  Destination:  {destination}")

    tx = build_proxy_recovery_tx(w3, eoa, destination, transfers)
    estimated_fee = tx["gas"] * tx["gasPrice"]
    print(f"  Gas estimate: {tx['gas']}")
    print(f"  Gas price:    {Web3.from_wei(tx['gasPrice'], 'gwei')} gwei")
    print(f"  Max fee:      {Web3.from_wei(estimated_fee, 'ether')} {NATIVE_GAS_SYMBOL}")

    if native_balance < estimated_fee:
        raise SystemExit(
            f"Insufficient {NATIVE_GAS_SYMBOL} on EOA for gas: "
            f"need up to {Web3.from_wei(estimated_fee, 'ether')} {NATIVE_GAS_SYMBOL}"
        )

    answer = input("\nBroadcast this transaction? [y/N]: ").strip().lower()
    if answer not in ("y", "yes"):
        raise SystemExit("Aborted by user; no transaction sent.")

    print("\nBroadcasting proxy recovery transaction...")

    signed = Account.sign_transaction(tx, private_key)
    raw_tx = getattr(signed, "rawTransaction", None) or getattr(signed, "raw_transaction")
    tx_hash = w3.eth.send_raw_transaction(raw_tx)
    print(f"\nBroadcasted: {tx_hash.hex()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
