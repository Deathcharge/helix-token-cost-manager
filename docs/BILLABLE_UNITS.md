# Non-token billable units

Provider bills often include quantities that are not input or output tokens: grounded requests, tool calls, cache storage, images, containers, or runtime. Samsarix records these as explicit provider/SKU charges instead of forcing them into token buckets.

## Price contract

A billable-unit price contains:

- exact `provider`, `sku`, `price_plan`, `service_tier`, and `region` selectors;
- a human-readable `unit`, such as `request`, `image`, or `token-hour`;
- `rate_usd` for one `pricing_quantity`; and
- an inclusive `effective_from` timestamp.

Lookup uses exact selectors and the newest price effective at the event time. It never falls back to another plan, tier, region, or SKU. The selected unit, rate, pricing quantity, selectors, and timestamp are snapshotted into the immutable charge event.

```bash
samsarix-cost --db costs.sqlite3 unit-price set \
  --provider example --sku grounding-query --unit request \
  --rate 35 --pricing-quantity 1000 --effective-from 2026-01-01

samsarix-cost --db costs.sqlite3 charge estimate \
  --provider example --sku grounding-query --quantity 1200 \
  --at 2026-07-01
```

The exact calculation is `quantity × rate_usd / pricing_quantity`, quantized to `0.000000000001` USD. Quantities and pricing quantities are positive finite decimals, which supports fractional runtime or storage units without binary floating-point drift. A zero rate is allowed for explicitly free usage.

## Recording and allocation

```bash
samsarix-cost --db costs.sqlite3 charge record \
  --provider example --sku grounding-query --quantity 1200 \
  --request-id response-123 --project research \
  --dimension team=product --dimension environment=production

samsarix-cost --db costs.sqlite3 charge report \
  --dimension environment=production --group-by dimension:team
```

Charge idempotency is scoped to request ID, provider, and SKU. One provider response may therefore record token usage and multiple distinct SKU charges without colliding. Repeating an identical charge returns the stored event; conflicting reuse fails.

Global and project budget spend includes both token usage events and charge events. Before a billable action, run `budget check --provider PROVIDER --sku SKU --quantity QUANTITY` with the applicable selectors and project. Reports aggregate charge counts and exact USD totals; they intentionally do not sum quantities across unlike units.

## Evidence and current boundary

[Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing) documents cache storage and grounding charges alongside token rates. [FOCUS 1.2](https://focus.finops.org/focus-specification/v1-2/) separates pricing quantity/unit, list or contracted unit price, region, and effective cost. The generic contract models those dimensions without embedding a provider catalog that can become stale.

SQLite schema version 4 adds billable-unit prices, immutable charge events, and bounded allocation dimensions. Versions 1 through 3 migrate transactionally on open.

Portable JSONL/CSV ledger version 2 and `ledger reconcile` remain token-event contracts. Charge events participate in `charge report` and budget totals, but mixed-event export/import and invoice reconciliation require a future ledger version. Provider invoices remain authoritative.
