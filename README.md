# Samsarix Token Cost Manager

Samsarix Token Cost Manager is a local-first Python library and CLI from Samsarix LLC for turning provider-reported LLM token usage into auditable USD cost records. It normalizes OpenAI, Anthropic, and OpenTelemetry GenAI usage without importing their SDKs, stores explicit time-versioned prices and immutable usage events in SQLite, attributes spend to business dimensions, and checks daily or monthly budgets before a model call.

It is for developers and small teams that need cost accounting without adopting an LLM gateway, hosted observability service, or private infrastructure. It never calls an LLM and has no runtime dependencies.

> **Maturity:** `0.1.0` release candidate. The core workflow and hosted CI matrix pass; the initial release tag and package publication remain owner-controlled release gates.

## What it solves

- Records the token counts returned by any provider or gateway.
- Ingests OpenAI, Anthropic, and OpenTelemetry GenAI JSON payloads directly.
- Applies the exact provider/model price that was effective when usage occurred.
- Prices cache reads and cache creation as separate, mutually exclusive buckets.
- Snapshots rates and calculated cost so historical records do not change later.
- Prevents duplicate accounting when a stable request ID is retried.
- Groups spend by provider, model, project, day, or month.
- Filters and groups by bounded allocation dimensions such as team, feature, environment, workflow, or customer tier.
- Checks global and per-project daily/monthly limits with automation-friendly exit codes.
- Keeps prompts, responses, credentials, and usage data on the local machine.

## Quick start

Prerequisites: Python 3.10 or newer and Git.

```bash
git clone https://github.com/Deathcharge/samsarix-token-cost-manager.git
cd samsarix-token-cost-manager
python -m venv .venv
python -m pip install .
```

Activate the environment first if your shell does not expose its installed scripts. You can also replace `samsarix-cost` below with `python -m samsarix_token_cost_manager`.

1. Add explicit pricing. These are illustrative rates for a fictional model, not a statement of any provider's current price:

   ```bash
   samsarix-cost --db costs.sqlite3 price set \
     --provider example \
     --model model-v1 \
     --input 2.50 \
     --output 10.00 \
     --cached-input 0.25 \
     --cache-write-input 3.125 \
     --effective-from 2026-01-01
   ```

2. Record provider-reported usage:

   ```bash
   samsarix-cost --db costs.sqlite3 record \
     --provider example \
     --model model-v1 \
     --input-tokens 1000000 \
     --output-tokens 500000 \
     --cached-input-tokens 100000 \
     --dimension team=platform \
     --dimension feature=assistant \
     --project demo \
     --request-id req-001
   ```

   Expected result:

   ```text
   Recorded evt_<generated-id>: $7.525
   ```

3. Review spend:

   ```bash
   samsarix-cost --db costs.sqlite3 report --group-by model
   ```

   ```text
   GROUP             REQUESTS  INPUT    OUTPUT  CACHE READ  CACHE WRITE  TOTAL USD
   ----------------  --------  -------  ------  ----------  -----------  ---------
   example/model-v1  1         1000000  500000  100000      0            $7.525
   ```

The database is created automatically. `samsarix-cost init` is available when an explicit initialization step is preferable.

## Ingest real provider and telemetry usage

Record an OpenAI Responses or Chat Completions JSON payload without adding the OpenAI SDK as a dependency:

```bash
samsarix-cost --db costs.sqlite3 ingest \
  --format openai \
  --file response.json \
  --project support-agent \
  --dimension team=customer-success \
  --dimension environment=production
```

`--format anthropic` understands Messages API cache-read and cache-creation fields. `--format otel` understands the OpenTelemetry `gen_ai.*` usage, provider, model, response, conversation, agent, workflow, service, and environment attributes. Use `--file -` to read one JSON object from standard input. The adapters store accounting metadata only—never prompts, responses, credentials, or message content.

Allocation dimensions support practical unit-economics questions:

```bash
# Cost by team for production support traffic
samsarix-cost --db costs.sqlite3 report \
  --dimension environment=production \
  --dimension feature=support \
  --group-by dimension:team
```

See [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md) for response fixtures, normalization rules, privacy guidance, and real deployment patterns.

## Check a budget before a call

```bash
samsarix-cost --db costs.sqlite3 budget set \
  --amount 25 \
  --period monthly \
  --project demo

samsarix-cost --db costs.sqlite3 budget check \
  --provider example \
  --model model-v1 \
  --input-tokens 2000 \
  --output-tokens 500 \
  --project demo
```

`budget check` evaluates both global and matching project budgets. It exits `0` when allowed, `3` when projected spend exceeds a limit, and `2` for invalid input or another expected product error. Recording already-incurred usage never drops the record; it prints a warning if the stored event leaves a budget exceeded.

For scripts, put `--json` before the command:

```bash
samsarix-cost --db costs.sqlite3 --json report --month 2026-07 --group-by project
```

## Python API

```python
from samsarix_token_cost_manager import CostManager, from_openai_response

with CostManager("costs.sqlite3") as costs:
    costs.set_price(
        provider="example",
        model="model-v1",
        input_usd_per_million="2.50",
        output_usd_per_million="10.00",
        cached_input_usd_per_million="0.25",
        effective_from="2026-01-01",
    )
    result = costs.record(
        provider="example",
        model="model-v1",
        input_tokens=1_000,
        output_tokens=250,
        request_id="req-001",
        project="demo",
    )
    print(result.event.cost.total_usd)

# Given a completed SDK `response` (or its JSON/dict representation):
measurement = from_openai_response(response)
with CostManager("costs.sqlite3") as costs:
    costs.record_measurement(
        measurement,
        project="support-agent",
        dimensions={"team": "customer-success", "environment": "production"},
    )
```

The deliberate public API is exported from `samsarix_token_cost_manager`: `CostManager`, its value objects, and expected exception types. Provider SDKs are intentionally not dependencies; pass their returned usage counts into `record`.

## Pricing and usage contract

- Rates are USD per one million units and are stored as decimal strings.
- `input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`, and `output_tokens` are mutually exclusive buckets. OpenAI and OpenTelemetry adapters subtract cache subsets from their inclusive input totals; Anthropic already reports separate buckets.
- Provider-reported usage is preferred. This package does not guess tokens from prompts or responses.
- Price matching is exact on `provider` and `model`; it does not use fuzzy aliases that could silently apply the wrong rate.
- `effective_from` and all budget periods use UTC. A date means midnight UTC.
- Historical events contain the selected rate version and calculated cost. Updating a price affects later records only.
- Cost is quantized to one trillionth of a USD using `Decimal`, avoiding binary floating-point drift.

## Configuration and persistence

`--db PATH` chooses the SQLite database. Without it, `SAMSARIX_COST_DB` is used. Otherwise the default is:

- Windows: `%LOCALAPPDATA%\samsarix-token-cost-manager\costs.sqlite3`
- macOS/Linux: `$XDG_DATA_HOME/samsarix-token-cost-manager/costs.sqlite3`, or `~/.local/share/...`

SQLite WAL mode and a five-second busy timeout support concurrent local processes. One `CostManager` instance also serializes access between threads. For a filesystem backup, close every connection before copying the database and its sidecar files. For an online backup, use SQLite's backup API (available as `sqlite3.Connection.backup()` in Python) or `VACUUM INTO`; do not sequentially copy live database and WAL files.

The package stores provider/model names, token counts, timestamps, optional request IDs, optional project labels, bounded allocation dimensions, rates, and costs. It does not store prompts, responses, API keys, user content, or network endpoints. Dimension values are operator-supplied accounting metadata and should use pseudonymous or non-sensitive identifiers.

## Command reference

```text
samsarix-cost init
samsarix-cost price set|list
samsarix-cost estimate
samsarix-cost record
samsarix-cost ingest
samsarix-cost report
samsarix-cost budget set|check
```

Run `samsarix-cost --help` or any subcommand with `--help` for complete arguments. Human-readable output goes to stdout, expected error messages go to stderr, and `--json` provides stable machine-readable output.

## Development and verification

The runtime package has no third-party dependencies. Contributor tools are pinned:

```bash
python -m venv .venv
python -m pip install --requirement requirements-dev.txt
python -m ruff check .
python -m ruff format --check .
python -m mypy samsarix_token_cost_manager
python -m pytest
python -m build
python -m twine check dist/*
```

CI runs linting, formatting, type checks, and tests on Python 3.10, 3.11, and 3.14 on Linux plus Python 3.11 on Windows and macOS. A separate job builds, inspects, installs, and smoke-tests the wheel.

## Architecture

- `adapters.py`: dependency-free OpenAI, Anthropic, and OpenTelemetry usage normalization.
- `models.py`: bounded input validation and immutable decimal value objects.
- `manager.py`: schema migration, pricing, immutable records, allocation dimensions, exact summaries, and budget evaluation.
- `cli.py`: standard-library CLI with stable JSON and exit-code contracts.
- SQLite schema version `2`: explicit price history, cache-write accounting, usage events, allocation dimensions, and budget constraints. Version `1` databases migrate transactionally on open.

There is no server, frontend, provider client, telemetry exporter, authentication layer, or cloud component. The local operator and filesystem boundary are the security model.

## Security, privacy, and reliability

- No network calls, credentials, prompt logging, telemetry export, or secret configuration.
- SQL values are parameterized; report grouping is a fixed allowlist.
- Text, token counts, rates, and timestamps are bounded and validated.
- Unknown models fail closed instead of producing a zero or guessed cost.
- Request IDs provide idempotent record retries and reject conflicting reuse.
- Database schema versions newer than the installed package fail closed.
- Budget checks are advisory enforcement points; callers must run them before spending.

See [SECURITY.md](SECURITY.md) for the threat boundary and disclosure process.

## Limitations

- Pricing must be maintained by the operator; no bundled catalog can silently become stale.
- Only USD and token-based input/output/cache-read/cache-write rates are supported in schema version `2`.
- The adapters normalize completed response/telemetry payloads but do not wrap SDK calls or fetch provider data.
- There is no tokenizer, tiered/context-length pricing, non-token tool/runtime charging, bulk import/export command, invoice reconciliation, dashboard, or distributed aggregation.
- SQLite is intended for local/single-host use, not a shared network filesystem.
- Budget checks cannot stop calls made through unrelated code; integrate the exit code or Python result into the caller.

These are deliberate release boundaries. Planned work is prioritized in [docs/PRODUCTIZATION.md](docs/PRODUCTIZATION.md).

## Distribution and project status

The simplest release path is a source distribution and pure-Python wheel published to PyPI after owner approval. The name `samsarix-token-cost-manager` returned no PyPI project on July 28, 2026, but availability is not reserved until publication. No package has been published by this work.

Contributions are welcome through GitHub issues and pull requests; see [CONTRIBUTING.md](CONTRIBUTING.md). The productization decisions and exact baseline are recorded in [docs/PRODUCTIZATION.md](docs/PRODUCTIZATION.md).

## License, attribution, and support

Copyright 2026 Samsarix LLC. The software and documentation are licensed under the [Apache License 2.0](LICENSE), with attribution recorded in [NOTICE](NOTICE). Apache-2.0 permits commercial use and modification subject to its terms, preserves applicable notices, and includes an express patent grant. It does not license Samsarix names or marks; see [TRADEMARKS.md](TRADEMARKS.md).

For general inquiries, email `contact@samsarix.com`. For product support, email `support@samsarix.com`. Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
