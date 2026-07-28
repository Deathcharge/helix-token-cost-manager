# Changelog

All notable changes will be documented here. The project follows Semantic Versioning while its public API remains `0.x` and may still evolve with documented migration notes.

## Unreleased

- Owner gates: confirm the repository license text, choose the initial release tag, and approve package publication.

### Added

- Local SQLite price history, immutable usage records, and exact decimal cost snapshots.
- Provider/model/project/day/month reporting with stable JSON output.
- Global and per-project daily/monthly budget checks.
- Idempotent request IDs and conflict detection.
- Dependency-free Python API and `helix-cost` CLI.
- Cross-platform CI coverage, local tests, type checks, lint/format policy, package verification, security guidance, and productization documentation.

### Security

- Pinned GitHub Actions to verified release commits and minimized workflow permissions and credential persistence.
- Raised the supported floor to maintained Python 3.10+ and upgraded pytest/setuptools past identified security advisories.
- Added bounded scalar validation, terminal-control rejection, parameterized SQL regression coverage, private local database permissions, and fail-closed schema/price behavior.

### Removed

- Non-standalone LLM provider and agent-engine extracts that depended on private `helix-unified` modules and did not implement token-cost management.
- Unused web, database, provider, bot, queue, and data-science runtime dependencies.
