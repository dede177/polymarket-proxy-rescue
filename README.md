# Polymarket Proxy USDC Recovery Tool

Interactive Python wizard for recovering Polygon Native USDC accidentally sent to a Polymarket proxy wallet address.

## What it does

- Prompts for your private key with hidden input.
- Derives your EOA address from that key.
- Derives the deterministic Polymarket proxy wallet address from your EOA.
- Checks Polygon Native USDC balances on both addresses.
- If the proxy holds Native USDC, asks for a Polygon destination address.
- Builds, simulates, estimates gas for, and broadcasts a proxy recovery transaction after explicit confirmation.
- If the proxy is not deployed yet, the factory deploys it and transfers the funds in the same transaction.

## Install

```bash
python -m pip install -r requirements.txt
```

## Run

```bash
python recover_token.py
```

You will be prompted for:

1. private key
2. destination Polygon address
3. final `yes` confirmation before broadcasting

## Defaults

- Network: Polygon mainnet
- Token: Polygon Native USDC `0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359`
- RPC: `POLYMARKET_RPC_URL` if set, otherwise `https://polygon.drpc.org`

## Safety notes

- The private key is not accepted as a command-line argument.
- The private key is not read from environment variables or config files.
- The private key is not saved.
- The script simulates the transaction and estimates gas before asking for confirmation.
- You need MATIC on the EOA for gas.

## Scope

This tool is specialized for Polymarket's proxy wallet/factory flow on Polygon and Native USDC recovery.
