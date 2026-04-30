# Repository Guidelines

## Project Structure & Module Organization

This repository is a small Python CLI utility for recovering ERC-20 tokens sent to a Polymarket proxy wallet on Polygon.

- `recover_token.py` contains the full command-line wizard, blockchain constants, wallet-file parsing, RPC checks, transaction simulation, and broadcast flow.
- `README.md` documents user-facing install/run behavior and safety notes.
- `requirements.txt` pins the runtime dependencies (`web3`, `eth-account`).
- `.gitignore` excludes local Python caches, virtual environments, logs, `.env`, and `wallet.json`.

There is currently no separate `src/`, `tests/`, or assets directory; keep the single-file layout unless a change clearly benefits from modularization.

## Build, Test, and Development Commands

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt
python recover_token.py --wallet-file wallet.json
python -m py_compile recover_token.py
```

- Create and activate `.venv` before installing dependencies.
- Run the CLI with a wallet JSON produced by `polymarket wallet show --output json > wallet.json`.
- Use `py_compile` as the minimum syntax check before committing.

## Coding Style & Naming Conventions

Use Python 3 style with 4-space indentation, type hints where practical, and clear function names in `snake_case`. Keep constants such as contract addresses and chain metadata in uppercase near the top of `recover_token.py`. Prefer explicit error messages via `SystemExit` for user-facing CLI failures. Do not add new dependencies unless they materially simplify required Web3 behavior.

## Testing Guidelines

No automated test suite exists yet. For logic changes, add focused tests under `tests/` using `pytest`, especially for wallet parsing, checksum validation, private-key config loading, and proxy derivation. Name tests `test_<behavior>.py`. Until tests exist, verify with:

```bash
python -m py_compile recover_token.py
python recover_token.py --help
```

Avoid live broadcasts during routine validation; use safe read-only checks or mocks for transaction-building paths.

## Commit & Pull Request Guidelines

Recent history uses short imperative summaries, for example `Recover USDC.e and native USDC in one batched factory call`. Prefer concise, action-oriented commit subjects that explain the user-visible reason for the change. Pull requests should include: purpose, affected recovery flow, validation commands run, linked issue if any, and screenshots or terminal excerpts for CLI output changes.

## Security & Configuration Tips

Never commit wallet files, private keys, `.env`, or logs containing addresses plus secrets. Keep RPC customization in `POLYMARKET_RPC_URL` or the hidden `--rpc` option. Preserve the safety model: no private keys in CLI arguments or environment variables, simulate and estimate gas before broadcasting, and require explicit confirmation for live transactions.
