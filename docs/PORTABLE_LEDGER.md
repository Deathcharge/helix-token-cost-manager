# Portable ledger and invoice reconciliation

Samsarix Token Cost Manager can move immutable usage-cost evidence between databases without recalculating historical prices. The versioned ledger contract supports canonical JSON Lines and RFC 4180-compatible CSV.

## Export

```bash
samsarix-cost --db costs.sqlite3 ledger export \
  --format jsonl --file february.jsonl \
  --since 2026-02-01 --until 2026-03-01
```

The command writes to a temporary file in the destination directory, flushes it, and atomically replaces the destination. Existing files are refused unless `--force` is explicit. Its output includes record count, exact USD total, byte count, media type, and SHA-256.

Identical event sets produce identical bytes and hashes. Events are ordered by occurrence time and event ID; JSON keys and dimension keys are sorted; decimals never use binary floats or exponent notation. There is deliberately no export timestamp in the hashed artifact.

CSV is useful for data warehouses and finance tools:

```bash
samsarix-cost --db costs.sqlite3 ledger export --format csv --file february.csv
```

CSV fields are flat except `dimensions_json`, which is canonical JSON. CSV is a data interchange format, not a safe spreadsheet-formula boundary. Treat values as untrusted text when opening an artifact in spreadsheet software.

## Verify, dry-run, and import

Use the digest from export when moving an artifact across a trust boundary:

```bash
samsarix-cost --db restored.sqlite3 ledger import \
  --format jsonl --file february.jsonl \
  --sha256 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef \
  --errors-file import-errors.json \
  --dry-run
```

Remove `--dry-run` only after reviewing the result. JSONL and CSV imports:

- enforce 100 MiB and one-million-record limits;
- require UTF-8 and the exact version-1 manifest/header;
- validate every identifier, timestamp, dimension, token count, rate, and decimal;
- recompute each cost component and total from snapshotted tokens and rates;
- reject duplicate event or request identities within the artifact;
- verify the optional SHA-256 before parsing;
- analyze all database identity conflicts before writing;
- insert the accepted batch in one SQLite transaction; and
- treat an identical repeat as idempotent instead of duplicating spend.

When `--errors-file` is supplied, a rejected import atomically writes a deterministic JSON error record containing the source digest and validation/conflict message. The error ledger is refused if its destination already exists, preventing accidental loss of earlier evidence.

Import preserves event IDs, timestamps, dimensions, rates, component costs, and totals. It does not require the destination to have the source price catalog because each immutable event carries its price snapshot.

## Reconcile an invoice

```bash
samsarix-cost --db costs.sqlite3 ledger reconcile \
  --provider openai --period-start 2026-02-01 --period-end 2026-03-01 \
  --invoice-id inv-2026-02 --billed-usd 123.45 --tolerance 0.01
```

The signed variance is `billed total - local total`. Exit status is `0` when its absolute value is within tolerance and `4` otherwise. Place `--json` before `ledger` for stable JSON.

The provider invoice remains authoritative. A variance can reflect negotiated or tiered rates, batch discounts, credits, taxes, rounding adjustments, missing events, or charges this token-only ledger does not model. Reconciliation records evidence; it never mutates usage history to manufacture a match.

This design follows the FOCUS concepts of billed cost, billing currency, billing period, invoice identity, and provider identity without claiming that a token-usage export is a complete FOCUS dataset. See [FOCUS 1.2](https://focus.finops.org/focus-specification/v1-2/) and [RFC 4180](https://www.rfc-editor.org/rfc/rfc4180).

## Backup boundary

A ledger is not a complete database backup: unused price versions, budgets, and SQLite operational metadata are not included. Use SQLite's online backup API or a closed database copy when those items must be restored too.
