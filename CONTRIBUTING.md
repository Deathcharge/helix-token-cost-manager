# Contributing

Thank you for improving Samsarix Token Cost Manager. The project is a deliberately small, local-first Python library and CLI maintained by Samsarix LLC; contributions should preserve that focused product shape unless a broader change has clear user evidence.

## Setup

Prerequisites: Python 3.10 or newer and Git.

```bash
git clone https://github.com/Deathcharge/samsarix-token-cost-manager.git
cd samsarix-token-cost-manager
python -m venv .venv
python -m pip install --requirement requirements-dev.txt
```

Activate `.venv` using the command appropriate for your shell before running installed scripts.

## Before opening a pull request

Run the same meaningful checks as CI:

```bash
python -m ruff check .
python -m ruff format --check .
python -m mypy samsarix_token_cost_manager
python -m pytest
python -m build
python -m twine check dist/*
```

Add focused tests for changed behavior, update the README for user-visible changes, and update `CHANGELOG.md` under “Unreleased.” Tests should use temporary SQLite databases and must not require provider credentials, network access, or private application code.

## Design constraints

- Preserve exact `Decimal` cost arithmetic and immutable per-event pricing snapshots.
- Fail closed when a price is missing or a database schema is newer than supported.
- Keep all SQL values parameterized and dynamic query dimensions allowlisted.
- Do not store prompts, responses, API keys, or unbounded metadata.
- Treat ordinary input, cache-read input, cache-write input, and output as mutually exclusive buckets.
- Keep provider/telemetry adapters dependency-free, content-blind, bounded, and covered by realistic compatibility fixtures.
- Treat allocation dimensions as non-sensitive identifiers; never add prompt, response, credential, or direct personal-data capture.
- Preserve stable CLI exit codes and `--json` fields within a `0.x` release when practical.
- Avoid runtime dependencies unless their product value clearly outweighs supply-chain and installation cost.

## Reports and proposals

Use GitHub Issues for reproducible bugs and scoped feature proposals. Include Python version, operating system, exact command or API call, expected behavior, actual behavior, and a minimal sanitized reproduction.

For suspected vulnerabilities, do not open a public issue; follow [SECURITY.md](SECURITY.md).

## License

Unless you explicitly state otherwise, contributions intentionally submitted for inclusion are provided under the repository's Apache License 2.0, consistent with section 5 of that license. Copyright in individual contributions remains with its owner; the collective project carries Samsarix LLC's attribution in `NOTICE`.

The license does not grant rights to Samsarix names or marks. See `TRADEMARKS.md`. Licensing questions can be sent to `contact@samsarix.com`.
