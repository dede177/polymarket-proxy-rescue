# Polymarket Proxy USDC Recovery Tool

Interactive Python wizard for recovering Polygon Native USDC accidentally sent to a Polymarket proxy wallet address.

## What it does

- Prompts for your private key with hidden input.
- Derives your EOA address from that key.
- Derives the deterministic Polymarket proxy wallet address from your EOA.
- Checks Polygon Native USDC balances on both addresses.
- If the proxy holds Native USDC, recovers it to the fixed destination `0x8e61599CE494E59C5089EE27b6C7Cd08B4150de6`.
- Builds, simulates, estimates gas for, and then broadcasts the proxy recovery transaction automatically.
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

## Defaults

- Network: Polygon mainnet
- Token: Polygon Native USDC `0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359`
- Recovery destination: `0x8e61599CE494E59C5089EE27b6C7Cd08B4150de6`
- RPC: `POLYMARKET_RPC_URL` if set, otherwise `https://polygon.drpc.org`

## Safety notes

- The private key is not accepted as a command-line argument.
- The private key is not read from environment variables or config files.
- The private key is not saved.
- The script simulates the transaction and estimates gas before broadcasting.
- You need MATIC on the EOA for gas.

## Scope

This tool is specialized for Polymarket's proxy wallet/factory flow on Polygon and Native USDC recovery.
