#!/usr/bin/env python3
"""
Diagnose and recover ERC-20 tokens sent to a Polymarket proxy wallet.

Safety model:
- Private key is prompted with getpass; it is never read from env/config and never accepted as a CLI arg.
- Proxy-held Native USDC is recovered to the fixed Polygon destination address baked into this script.
- The script simulates and estimates gas, then broadcasts automatically.

Dependencies:
    python -m pip install web3 eth-account

Run:
    python scripts/recover_token.py

Default mode prompts for:
- your private key

Everything else is derived or defaulted:
- EOA address: derived from private key
- proxy address: derived from EOA
- token checked: Polygon Native USDC
- recovery destination: 0x8e61599CE494E59C5089EE27b6C7Cd08B4150de6
- RPC: POLYMARKET_RPC_URL or the built-in Polygon RPC default

The recovery transaction calls the Polymarket proxy factory. If the proxy has not
been deployed yet, the factory deploys it and transfers the proxy's token balance
to the fixed destination address in the same transaction.
"""

from __future__ import annotations

import argparse
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


POLYGON_CHAIN_ID = 137
DEFAULT_RPC_URL = "https://polygon.drpc.org"
DEFAULT_NATIVE_USDC = "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359"
DEFAULT_DESTINATION = "0x8e61599CE494E59C5089EE27b6C7Cd08B4150de6"

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
        description="Private-key-only Polymarket proxy Native USDC recovery wizard."
    )
    parser.add_argument(
        "--token",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--rpc", help=argparse.SUPPRESS)
    return parser.parse_args()


def banner() -> None:
    print()
    print("Polymarket ERC-20 Recovery Wizard")
    print("────────────────────────────────")
    print("Required input: your private key. The recovery address is fixed.")
    print("This checks both your EOA and derived Polymarket proxy wallet.")
    print("Default token checked: Polygon Native USDC.")
    print("Proxy-held tokens are recovered to the fixed destination baked into this script.")
    print("If recovery is possible, the transaction is sent automatically.")
    print()


def checksum_address(raw: str, label: str) -> str:
    try:
        return to_checksum_address(raw)
    except ValueError as exc:
        raise SystemExit(f"Invalid {label} address: {raw}") from exc


def derive_proxy_wallet(eoa_address: str) -> str:
    """Derive the same Polygon proxy address as polymarket_client_sdk::derive_proxy_wallet."""
    factory_bytes = to_bytes(hexstr=PROXY_FACTORY)
    salt = keccak(to_bytes(hexstr=eoa_address))
    init_code_hash = to_bytes(hexstr=PROXY_INIT_CODE_HASH)
    digest = keccak(b"\xff" + factory_bytes + salt + init_code_hash)
    return to_checksum_address(digest[-20:])


def require_polygon(w3: Web3) -> None:
    try:
        chain_id = w3.eth.chain_id
    except Exception as exc:  # noqa: BLE001 - display RPC failure clearly
        raise SystemExit(f"Could not connect to RPC: {exc}") from exc
    if chain_id != POLYGON_CHAIN_ID:
        raise SystemExit(f"RPC chain id is {chain_id}, expected Polygon mainnet {POLYGON_CHAIN_ID}")


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


def proxy_recovery_calldata(token_addr: str, to_addr: str, raw_amount: int) -> str:
    """Encode the reverse-engineered Polymarket proxy factory batch call.

    Selector 0x34ee9791 accepts an array of call actions. Action operation 1 is CALL:
      (operation=1, target=token, value=0, data=ERC20.transfer(to, amount))

    When sent to the proxy factory from the EOA, the factory resolves/deploys that
    EOA's deterministic proxy and executes the action through it.
    """
    transfer = erc20_transfer_calldata(to_addr, raw_amount)

    # ABI encoding for one argument:
    #   tuple(uint256 operation, address target, uint256 value, bytes data)[]
    encoded_actions = (
        word(32)          # offset to array
        + word(1)         # array length
        + word(32)        # offset to first dynamic tuple
        + word(1)         # operation: CALL
        + address_word(token_addr)
        + word(0)         # native value
        + word(128)       # offset to bytes data inside tuple
        + encode_bytes(transfer)
    )
    return "0x34ee9791" + encoded_actions


def build_proxy_recovery_tx(
    w3: Web3,
    from_addr: str,
    token_addr: str,
    to_addr: str,
    raw_amount: int,
) -> dict[str, Any]:
    data = proxy_recovery_calldata(token_addr, to_addr, raw_amount)

    tx = {
        "from": from_addr,
        "to": checksum_address(PROXY_FACTORY, "proxy factory"),
        "data": data,
        "nonce": w3.eth.get_transaction_count(from_addr),
        "chainId": POLYGON_CHAIN_ID,
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
    token_addr: str,
    symbol: str,
    decimals: int,
    eoa: str,
    proxy: str,
    eoa_balance: int,
    proxy_balance: int,
    proxy_deployed: bool,
    native_balance_wei: int,
) -> None:
    print("\nWallets")
    print(f"  EOA:          {eoa}")
    print(f"  Proxy:        {proxy}")
    print(f"  Proxy code:   {'deployed' if proxy_deployed else 'not deployed'}")
    print("\nToken")
    print(f"  Contract:     {token_addr}")
    print(f"  Symbol:       {symbol}")
    print(f"  Decimals:     {decimals}")
    print("\nBalances")
    print(f"  EOA token:    {human_amount(eoa_balance, decimals)} {symbol}")
    print(f"  Proxy token:  {human_amount(proxy_balance, decimals)} {symbol}")
    print(f"  EOA MATIC:    {Web3.from_wei(native_balance_wei, 'ether')} MATIC")


def main() -> int:
    args = parse_args()
    import_deps()

    banner()

    print("Step 1/3: Enter the private key")
    print("  The key is hidden as you type and is not saved.")
    private_key = getpass("Enter private key: ").strip()
    if not private_key:
        raise SystemExit("Private key is required")
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key

    try:
        account = Account.from_key(private_key)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"Invalid private key: {exc}") from exc

    print("\nStep 2/3: Derive wallet addresses")
    eoa = to_checksum_address(account.address)
    proxy = derive_proxy_wallet(eoa)

    print("  ✓ Wallet loaded")
    print(f"  EOA:   {eoa}")
    print(f"  Proxy: {proxy}")

    print("\nStep 3/3: Connect, scan, and recover Native USDC")
    rpc_url = args.rpc or os.environ.get("POLYMARKET_RPC_URL", DEFAULT_RPC_URL)
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    require_polygon(w3)
    print("  ✓ Connected to Polygon mainnet")

    token_addr = checksum_address(args.token or DEFAULT_NATIVE_USDC, "token")
    if args.token:
        print(f"  Token override: {token_addr}")
    else:
        print(f"  Token: Polygon Native USDC ({token_addr})")

    token = w3.eth.contract(address=token_addr, abi=ERC20_ABI)
    decimals = int(call_or_default(token.functions.decimals, 18))
    symbol = str(call_or_default(token.functions.symbol, "TOKEN"))

    eoa_balance = int(token.functions.balanceOf(eoa).call())
    proxy_balance = int(token.functions.balanceOf(proxy).call())
    proxy_deployed = len(w3.eth.get_code(proxy)) > 0
    native_balance = int(w3.eth.get_balance(eoa))

    print_summary(
        token_addr,
        symbol,
        decimals,
        eoa,
        proxy,
        eoa_balance,
        proxy_balance,
        proxy_deployed,
        native_balance,
    )

    if proxy_balance == 0:
        print("\nNo proxy token balance to recover.")
        if eoa_balance > 0:
            print("EOA already holds token balance controlled by this private key.")
        return 0

    print("\nProxy token balance detected")
    if not proxy_deployed:
        print("  Proxy code is not deployed yet; the recovery transaction will deploy it first.")

    destination = checksum_address(DEFAULT_DESTINATION, "default destination")
    print(f"  Recovery destination: {destination}")

    raw_amount = proxy_balance
    pretty_amount = human_amount(raw_amount, decimals)

    print("\nProxy recovery transaction")
    print(f"  Amount:       {pretty_amount} {symbol}")
    print(f"  Destination:  {destination}")

    tx = build_proxy_recovery_tx(w3, eoa, token_addr, destination, raw_amount)
    estimated_fee = tx["gas"] * tx["gasPrice"]
    print(f"  Gas estimate: {tx['gas']}")
    print(f"  Gas price:    {Web3.from_wei(tx['gasPrice'], 'gwei')} gwei")
    print(f"  Max fee:      {Web3.from_wei(estimated_fee, 'ether')} MATIC")

    if native_balance < estimated_fee:
        raise SystemExit(
            "Insufficient MATIC on EOA for gas: "
            f"need up to {Web3.from_wei(estimated_fee, 'ether')} MATIC"
        )

    print("  Broadcast:    automatic")
    print("\nBroadcasting proxy recovery transaction...")

    signed = Account.sign_transaction(tx, private_key)
    raw_tx = getattr(signed, "rawTransaction", None) or getattr(signed, "raw_transaction")
    tx_hash = w3.eth.send_raw_transaction(raw_tx)
    print(f"\nBroadcasted: {tx_hash.hex()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
