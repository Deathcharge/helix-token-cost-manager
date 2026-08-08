# Provider and telemetry integrations

Samsarix Token Cost Manager accepts completed provider responses and OpenTelemetry GenAI attributes without importing a provider SDK, calling a network endpoint, or retaining prompt/response content. The integration boundary is a Python object or JSON mapping containing model identity and reported usage.

## Why this boundary

Provider-reported usage is the most reliable per-request source. [Langfuse documents](https://langfuse.com/docs/observability/features/token-and-cost-tracking) the same preference for ingested usage over inference and normalizes inclusive OpenTelemetry cache counts into mutually exclusive buckets. The [OpenTelemetry GenAI conventions](https://github.com/open-telemetry/semantic-conventions-genai) provide vendor-neutral provider, model, response, conversation, agent, workflow, and usage attributes. FinOps guidance treats [allocation metadata](https://www.finops.org/framework/capabilities/allocation/) and [unit economics](https://www.finops.org/framework/capabilities/unit-economics/) as necessary to connect technology spend to responsible teams and business value.

The package therefore separates two concerns:

1. The caller or instrumentation library performs the model request and receives usage.
2. Samsarix normalizes mutually exclusive billable buckets, snapshots an explicit price, and records bounded allocation metadata locally.

## Python API

```python
from samsarix_token_cost_manager import CostManager, from_openai_response

response = openai_client.responses.create(...)
measurement = from_openai_response(response)

with CostManager("costs.sqlite3") as costs:
    costs.record_measurement(
        measurement,
        project="support-agent",
        dimensions={
            "team": "customer-success",
            "feature": "ticket-resolution",
            "environment": "production",
        },
    )
```

`from_openai_response`, `from_anthropic_response`, and `from_otel_attributes` return an immutable `UsageMeasurement`. Every value is validated again when it is recorded. A stable provider response ID becomes the request ID, making retries idempotent.

## OpenAI normalization

The adapter supports current Responses API names (`input_tokens`, `output_tokens`, `input_tokens_details.cached_tokens`) and legacy Chat Completions names (`prompt_tokens`, `completion_tokens`, `prompt_tokens_details.cached_tokens`). OpenAI defines cached input as a subset of its inclusive input total, so the adapter records:

```text
ordinary input = reported input - cached input
cache read     = cached input
output         = reported output
```

This matches the inclusive cache semantics in the [OpenAI Responses usage schema](https://platform.openai.com/docs/api-reference/responses/object#responses/object-usage). If the cache subset exceeds total input, ingestion fails closed.

Minimal JSON fixture:

```json
{
  "id": "resp_123",
  "model": "gpt-5-mini-2025-08-07",
  "service_tier": "default",
  "usage": {
    "input_tokens": 1000,
    "output_tokens": 250,
    "input_tokens_details": {"cached_tokens": 800}
  }
}
```

## Anthropic normalization

Anthropic reports ordinary input, cache reads, cache creation, and output separately. Samsarix preserves those four buckets. Configure cache-creation pricing explicitly because Anthropic documents different multipliers for cache writes and reads in its [prompt-caching pricing](https://platform.claude.com/docs/en/about-claude/pricing#prompt-caching).

```bash
samsarix-cost price set \
  --provider anthropic \
  --model claude-sonnet-5 \
  --input 3 \
  --output 15 \
  --cached-input 0.30 \
  --cache-write-input 3.75
```

Minimal JSON fixture:

```json
{
  "id": "msg_123",
  "model": "claude-sonnet-5",
  "usage": {
    "input_tokens": 100,
    "output_tokens": 40,
    "cache_read_input_tokens": 500,
    "cache_creation_input_tokens": 200
  }
}
```

## OpenTelemetry GenAI normalization

The OTel adapter consumes these usage fields:

- `gen_ai.usage.input_tokens`
- `gen_ai.usage.output_tokens`
- `gen_ai.usage.cache_read.input_tokens`
- `gen_ai.usage.cache_creation.input_tokens`

It also maps these non-content attributes into accounting identity or allocation dimensions:

| Attribute | Samsarix field |
|---|---|
| `gen_ai.provider.name` | provider |
| `gen_ai.response.model`, then `gen_ai.request.model` | model |
| `gen_ai.response.id` | idempotent request ID |
| `gen_ai.conversation.id` | `conversation` dimension |
| `gen_ai.agent.name` | `agent` dimension |
| `gen_ai.agent.version` | `agent_version` dimension |
| `gen_ai.workflow.name` | `workflow` dimension |
| `service.name` | `service` dimension |
| `deployment.environment.name` | `environment` dimension |

No message, prompt, output, tool argument, or system-instruction attributes are read or stored.

## CLI ingestion

```bash
# File
samsarix-cost --db costs.sqlite3 ingest \
  --format otel \
  --file span-attributes.json \
  --project agent-platform \
  --dimension team=platform

# Pipeline
producer-command | samsarix-cost --db costs.sqlite3 ingest \
  --format anthropic \
  --file - \
  --dimension environment=production
```

Input must be one UTF-8 JSON object and is capped at 10 MiB. Extra fields are ignored. Missing or inconsistent usage fails closed with exit code `2`.

## Real use cases

### Customer-support unit economics

Attach `feature=ticket-resolution`, a pseudonymous customer tier, team, and environment. Group cost by `dimension:team` or `dimension:customer_tier`; divide the exported total by resolved-ticket counts in the business analytics system. Do not place ticket text or direct customer identifiers in dimensions.

### Agent and workflow regression control

OTel automatically supplies agent, agent version, workflow, conversation, and service context. Compare cost across `dimension:agent_version` after a prompt or routing change, and apply project budgets before expensive calls.

### Multi-tenant SaaS chargeback

Use a stable pseudonymous tenant key as a dimension and a product area as `project`. Exact event snapshots make later price changes non-retroactive. Provider invoices remain the financial system of record until a future reconciliation feature is implemented.

### CI and coding-agent spend

Use dimensions such as repository, workflow, branch class, or task type. This exposes cost per successful build, pull request, or automated maintenance task without sending code or prompts to an additional observability service.

## Trust and privacy boundary

- Treat every provider/telemetry payload as untrusted input.
- Use only non-sensitive or pseudonymous dimensions; values are stored in plaintext SQLite.
- Do not pass full span exports containing prompt or response fields when a minimal attribute mapping is available.
- Provider dashboards and invoices remain authoritative for billed totals; Samsarix is an application-side allocation and control ledger.
- The adapters intentionally perform no model aliasing or price lookup over the network. An unknown provider/model fails closed until the operator configures an exact price.
