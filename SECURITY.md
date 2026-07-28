# Security Policy

## Supported versions

Until the first published release, the current `main` branch and the `0.1.x` line are the supported review targets. A formal support window should be defined by the owner when releases are published.

## Report a vulnerability

Use GitHub's private **Report a vulnerability** / Security Advisory flow for this repository. If that flow is unavailable, email `support@samsarix.com` with `[SECURITY]` in the subject. Do not include secrets, credentials, private prompts, or personal data in a public issue.

Include the affected version/commit, operating system and Python version, the exact trust boundary, a minimal sanitized reproduction, impact, and any suggested fix. Maintainers should acknowledge, validate, remediate, and coordinate disclosure before publishing details.

## Product threat boundary

This package is a local library and CLI. It opens an operator-selected SQLite path and accepts operator- or application-supplied provider/model identifiers, token counts, timestamps, request IDs, project labels, rates, and budget limits. It does not listen on a network port, call providers, execute plugins, deserialize arbitrary Python objects, or require credentials.

Security invariants:

- No prompts, responses, API keys, authentication tokens, or individual token content are intentionally stored or logged. Aggregate token counts are accounting data and are stored.
- All SQL values are parameterized; selectable report dimensions are fixed by an allowlist.
- Untrusted scalar inputs are finite, length-bounded, type-checked, and range-checked.
- Unknown prices fail closed and historical event prices remain immutable.
- A request ID cannot be reused for a different usage tuple.
- A newer database schema is never modified by an older package.
- Database creation uses private file/directory permissions where the platform supports them.
- Resource use is bounded per scalar input; summary queries stream rows rather than loading the full event set. Total disk use, scan time, and group cardinality still grow with retained history.

Assumptions and limitations:

- The local operating-system account and selected database directory are trusted. An attacker who can replace or edit the SQLite database can alter accounting records.
- Project and request labels may become sensitive if callers put personal data in them; use opaque identifiers and protect backups.
- SQLite WAL files contain recent records and must receive the same protection as the main database.
- Budget checks are advisory. The calling application is responsible for checking before it performs billable work and for handling concurrent spend between a check and the provider call.
- Pricing accuracy depends on operator-maintained rates and provider-reported token buckets.
- SQLite is not supported on an untrusted/shared network filesystem.

## Dependency and secret posture

The installed package has no third-party runtime dependencies and performs no network requests or telemetry. Build and contributor tools are pinned in `requirements-dev.txt`; CI should review dependency updates rather than applying sweeping upgrades. Never commit provider credentials or production usage databases.
