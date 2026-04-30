# Polymarket Proxy USDC Recovery Tool

Interactive Python wizard for recovering USDC.e and native USDC accidentally sent to a Polymarket proxy wallet address on Polygon.

## What it does

- Loads your EOA and proxy wallet addresses from a JSON file produced by `polymarket wallet show --output json`.
- Verifies the wallet file's `proxy_address` matches the deterministic proxy derived from the EOA.
- Loads the private key from the wallet file's `config_path` when present, otherwise prompts with hidden input.
- Verifies the private key controls the wallet file's EOA before continuing.
- Checks both Polygon USDC.e and native USDC balances on the EOA and proxy.
- Recovers proxy-held token balances back to the EOA from the wallet file.
- Builds, simulates, estimates gas for, and asks for confirmation before broadcasting the recovery transaction.
- If the proxy is not deployed yet, the factory deploys it and transfers the funds in the same transaction.

## Install

```bash
python -m pip install -r requirements.txt
```

## Run

Create a wallet file with the Polymarket CLI:

```bash
polymarket wallet show --output json > wallet.json
```

Then run the recovery wizard:

```bash
python recover_token.py --wallet-file wallet.json
```

You may be prompted for your private key if the wallet file does not include a usable `config_path`.

## Defaults

- Network: Polygon mainnet
- Tokens checked by default:
  - USDC.e `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174`
  - Native USDC `0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359`
- Recovery destination: the EOA address from `wallet.json`
- RPC: `POLYMARKET_RPC_URL` if set, otherwise `https://polygon.drpc.org`

## Safety notes

- Do not use a wallet JSON supplied by someone else.
- The wallet file must contain `address` and `proxy_address`; `config_path` is optional.
- The private key is never accepted as a command-line argument or environment variable.
- The private key is not saved or printed.
- The script verifies both proxy derivation and private-key ownership before building a transaction.
- The script simulates the transaction and estimates gas before asking for broadcast confirmation.
- You need POL/MATIC on the EOA for gas.

## Scope

This tool is specialized for Polymarket's proxy wallet/factory flow on Polygon and USDC.e/native USDC recovery.
