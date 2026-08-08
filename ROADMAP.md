# Samsarix Token Cost Manager roadmap

This roadmap separates four gates: merge, release, publication, and flagship adoption. Passing one does not imply the next.

## Product boundary

Portfolio role: **reusable library or sdk**. Keep this as a small, independently versioned package. Samsarix Unified should consume it only through a public API adapter; private monorepo imports and copied implementations are out of scope.

Current disposition: The productized default is merged. The current release candidate includes dependency-free provider/OpenTelemetry ingestion, cache-write accounting, and allocation dimensions; release and adoption remain separate decisions.

## Stabilize the productized default

- Keep the default branch buildable from a clean checkout and preserve exact-head CI evidence.
- Keep Samsarix LLC branding, package identity, license metadata, and compatibility aliases internally consistent.
- Preserve the pre-productization default under a rollback ref before merging; do not delete legacy history.
- Review priority: Stabilize and consume the versioned provider/telemetry and portable-ledger contracts before approving a tagged `0.1` wheel.

## Release candidate

- Build and install the wheel in a clean environment.
- Prove one real consumer and a versioned compatibility fixture.
- Publish only after package-name ownership, licensing, provenance, and rollback are recorded.

Current hardening backlog:

- Provider and OpenTelemetry adapters have conformance fixtures but no production adopter or package release yet.
- Operator pricing can be stale/wrong while remaining internally consistent.
- Advisory budgets cannot reserve spend atomically across distributed callers.
- Portable event import/export is present; full database backup/restore, retention, signatures, and an external append-only audit log are not.
- Total-level reconciliation is present; provider invoice-file adapters, tiered/context pricing, non-token charges, forecasting, and anomaly detection are not.
- Public API/database schema creates ongoing compatibility and migration duties.
- Owner must approve package namespace, Apache licensing authority, tag, and trusted publishing.

## Competitive milestones

1. **Interoperable ingestion and allocation — landed:** OpenAI, Anthropic, and OpenTelemetry normalization; cache-read/cache-write accounting; schema `1` to `2` migration; bounded dimensions; dimension filters/groups; CLI JSON ingestion.
2. **Portable ledger and reconciliation — active:** deterministic JSONL/CSV export, atomic artifact writes and transactional restore, import validation/dry-run, provider-invoice comparison, and stable artifact digests. Provider-specific invoice-file adapters and full database backup remain follow-up work.
3. **Pricing fidelity:** threshold/tier pricing, batch and residency modifiers, non-token tool/runtime units, and explicit negotiated-rate overlays.
4. **Guardrails:** atomic local reservations, alerts, forecasts, anomalies, and optimization recommendations with explainable evidence.
5. **Adoption:** one Samsarix consumer contract, production signal, published package provenance, support window, and migration policy.

## Samsarix adoption

- Define a public API, event, schema, artifact, or deployment contract before connecting to Samsarix Unified.
- Add a consumer-owned contract fixture covering authentication, privacy, limits, errors, and version compatibility.
- Make one implementation canonical; remove or freeze duplicate behavior only after parity and rollback are proven.
- Record an owner, support level, compatibility window, and measurable adoption signal.

## Completion evidence

A milestone is complete only when its exact commit, commands and results, artifact digest, consumer or deployment, and rollback path are recorded in a pull request or release record. README claims must not exceed that evidence.
