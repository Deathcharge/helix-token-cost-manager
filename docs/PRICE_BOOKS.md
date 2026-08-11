# Explicit price books

Samsarix resolves each token charge against an operator-maintained price-book rule. It does not download or bundle provider rates. This keeps a stale public catalog from silently changing historical cost and lets an organization represent its own contract exactly.

## Selector contract

A rule is selected by exact `provider`, `model`, `price_plan`, `service_tier`, and `region`, then by the request's total rated input tokens and event timestamp. The defaults are `price_plan=list`, `service_tier=standard`, and `region=global`.

Total rated input is the sum of non-cached input, cache-read input, and cache-creation input. Threshold bounds are inclusive. Rules with the same selector and effective timestamp may not overlap. A missing selector or threshold fails closed; Samsarix never falls back to another plan, tier, geography, or model.

The selected rule and its threshold bounds are snapshotted into each immutable event and portable ledger record. Later price changes therefore do not rewrite history.

## Examples

Long-context bands:

```bash
samsarix-cost price set --provider example --model model-v1 \
  --input 3 --output 15 --input-token-max 200000 \
  --effective-from 2026-01-01

samsarix-cost price set --provider example --model model-v1 \
  --input 6 --output 22.5 --input-token-min 200001 \
  --effective-from 2026-01-01
```

Batch, residency, and negotiated price books are independent selectors:

```bash
samsarix-cost price set --provider example --model model-v1 \
  --service-tier batch --input 1.5 --output 7.5 \
  --effective-from 2026-01-01

samsarix-cost price set --provider example --model model-v1 \
  --price-plan enterprise-2026 --service-tier priority --region us \
  --input 4 --output 20 --effective-from 2026-01-01

samsarix-cost record --provider example --model model-v1 \
  --price-plan enterprise-2026 --service-tier priority --region us \
  --input-tokens 1000 --output-tokens 250
```

Use the service tier actually reported by the provider when available. OpenAI documents that the response tier can differ from the requested tier. Provider adapters retain reported service-tier metadata as an allocation dimension, while price selection remains explicit so normalization cannot silently choose a contract rate.

## Why these selectors exist

Current provider documentation shows that pricing is not a single model rate:

- [OpenAI API pricing](https://developers.openai.com/api/docs/pricing) distinguishes standard, batch, priority, flex, long-context, and data-residency pricing. Its [Responses API contract](https://platform.openai.com/docs/api-reference/responses-streaming) reports the service tier actually used.
- [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing) documents long-context thresholds, batch discounts, cache rates, inference geography premiums, marketplace units, and negotiated discounts.
- [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing) distinguishes standard, batch, flex, priority, cache storage, and non-token grounding charges.
- [FOCUS 1.2](https://focus.finops.org/focus-specification/v1-2/) separates list and contracted unit price, pricing quantity/unit, region, and effective cost.

Those sources motivate a provider-neutral selector model. They are not copied into a live catalog, and the examples above are illustrative rather than assertions of current provider prices.

## Compatibility

SQLite schema version 3 introduced selector-aware token prices; current schema version 4 transactionally migrates schema 1, 2, or 3 databases and adds separate billable-unit prices. Portable ledger version 2 records token selectors and threshold provenance. JSONL and CSV version 1 artifacts remain importable with those same defaults.

Non-token SKUs such as cache storage, grounding queries, tool calls, containers, and runtime are intentionally separate from token price books. They require an explicit quantity/unit charge model rather than pretending they are tokens.
