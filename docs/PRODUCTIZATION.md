# Productization Record

Last updated: July 28, 2026  
Baseline revision: `16685f129672c97a630ee04066cb8eb379bd1531` (`main`, matching `origin/main`)  
Working-tree baseline: clean; no pre-existing tracked or untracked changes

## Current repository assessment

The repository was extracted from `helix-unified` on June 16, 2026 with the stated purpose “Token tracking and cost calculation for LLM usage.” The extraction did not contain token tracking or cost calculation. Its two Python files were an LLM service client and a large personality/agent engine copied from the flagship application. They referenced missing relative modules and private `apps.backend` packages from a locally discoverable `helix-unified` checkout.

The package directory had no `__init__.py`, so `setuptools.find_packages()` found no package. `python -m build` exited successfully but produced a wheel containing only metadata and the license. Installation could not resolve the unreleased `helix-hub-shared>=0.1.0` dependency. There were no tests, examples, CI workflows, cost APIs, CLI, or persistence. The README claimed all of those existed, called the project production-ready, and said MIT while the repository contained customized Business Source License text.

The original source remains recoverable from Git history. The non-standalone provider/agent files were removed from the release package because they neither supported the repository's product identity nor worked independently.

## Chosen product definition

**Product:** Samsarix Token Cost Manager, a dependency-free, local-first Python library and CLI from Samsarix LLC for explicit LLM token-price management, immutable usage-cost recording, exact spend reporting, and pre-call budget checks.

**Target user:** an application developer or small team receiving token counts from one or more provider SDKs who wants auditable cost data without deploying a gateway or hosted observability system.

**Primary journey:** install the wheel; add exact time-versioned pricing; record a provider-reported usage event with an idempotency key; inspect spend by model/project/month; check an applicable budget before the next call.

**Independent reason to exist:** LiteLLM is primarily a broad provider SDK/gateway and Langfuse is an observability platform. This package is a much smaller offline accounting component with no network, provider, service, or credential dependency. It can complement either product or a direct provider integration.

**Deliberately out of scope for `0.1`:** provider calls, tokenization, prompt/response storage, telemetry, a web UI, authentication, cloud services, subscriptions, automatic price downloads, fuzzy model aliases, tiered/context-sensitive pricing, multi-currency accounting, import/export adapters, and distributed databases.

## Evidence from current ecosystem research

- The [Python Packaging User Guide](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/) supports one PEP 621 `pyproject.toml` and a `[project.scripts]` console entry point. The project now follows that shape.
- [Langfuse token/cost tracking](https://langfuse.com/docs/observability/features/token-and-cost-tracking) treats usage buckets as mutually exclusive, prioritizes ingested/provider usage, supports explicit custom prices, and snapshots inferred cost at ingestion. Those principles informed this package's token contract and immutable rate snapshots.
- [LiteLLM](https://docs.litellm.ai/) demonstrates demand for provider-neutral cost tracking, but its SDK/gateway scope and large provider catalog support a distinct lightweight offline wedge here.
- [GitHub Actions' maintained actions](https://github.com/actions/setup-python/releases) were at `setup-python` v6 in the bounded July 2026 check; CI pins the reviewed `checkout` v6.0.2 and `setup-python` v6.2.0 release commits instead of mutable major tags.
- The [Python release-status table](https://devguide.python.org/versions/) marks Python 3.9 end-of-life. The supported floor is Python 3.10 so the test stack can use pytest 9.0.3, which fixes [GHSA-6w46-j5rx-g56g](https://github.com/advisories/GHSA-6w46-j5rx-g56g).
- The isolated build backend is pinned to setuptools 83.0.0, the fixed boundary for its [source-distribution exclusion bypass advisory](https://github.com/advisories/GHSA-h35f-9h28-mq5c).
- The PyPI JSON endpoint for `samsarix-token-cost-manager` returned `404` on July 28, 2026. That is evidence only that no project was visible then, not a reservation or permission to publish.

No product-market fit or validated commercial demand is claimed.

## Ownership, branding, and licensing decision

On July 28, 2026, the owner identified the operating company as **Samsarix LLC** and designated `contact@samsarix.com` and `support@samsarix.com` as the working contact addresses. Because no package had been published, the distribution, import package, CLI, environment variable, platform data path, metadata, and documentation were renamed together rather than preserving a misleading Helix compatibility surface.

The owner asked for the strongest practical licensing fit for attribution and protection. The repository now uses the unmodified [Apache License 2.0](https://www.apache.org/licenses/LICENSE-2.0.txt), a Samsarix `NOTICE`, SPDX package metadata, citation metadata, and separate trademark guidance. Apache-2.0 is [OSI approved](https://opensource.org/licenses), preserves applicable copyright/attribution notices, includes an express patent grant, and does not grant trademark rights. The previous customized BSL was removed because it named another work, contained conflicting change timing, restricted production adoption, and the BSL steward explicitly states that BSL 1.1 is not an Open Source license. This is an engineering/product recommendation, not legal advice; Samsarix LLC should have counsel review its broader IP and trademark program when commercially material.

## Key product and architecture decisions

- **Explicit rates, no bundled live catalog:** wrong-but-plausible cost is worse than a clear missing-price error. Operators own provider pricing freshness.
- **Provider-reported tokens:** the package does not infer reasoning/cached usage from content it cannot observe.
- **Mutually exclusive buckets:** normal input, cached input, and output cannot overlap by contract.
- **Decimal arithmetic and string persistence:** costs are quantized to `0.000000000001` USD; SQLite aggregation is done as streamed Python `Decimal` data to avoid float drift.
- **Effective-dated pricing:** exact provider/model matching selects the newest price at the event timestamp and snapshots it into the immutable event.
- **SQLite WAL:** the smallest durable local persistence with transactions, cross-process coordination, bounded wait, and no service dependency.
- **Idempotent record keys:** request ID retries return the existing identical event; conflicting reuse fails.
- **Advisory budgets:** checks return a stable denial code before spend. Post-call recording never discards incurred usage.
- **Minimal public surface:** only `CostManager`, public value objects, expected exceptions, and the CLI are exported.
- **No runtime dependencies:** reduces install, supply-chain, privacy, and compatibility risk.

## Assumptions

- The caller trusts its local OS account and database path.
- Provider usage counts and operator-entered prices are authoritative inputs.
- UTC daily/monthly accounting is acceptable for the first release.
- A project label is a non-sensitive opaque allocation key; callers will not place PII in it.
- Local/single-host SQLite is sufficient; network filesystems and multi-host writes are not supported.

## Baseline command results

Commands were run from the clean baseline on Python `3.11.9` with pip `26.1.1`:

| Command | Actual baseline result |
|---|---|
| `python -m compileall -q .` | Passed, but compilation did not prove imports or package contents. |
| `python -c "import helix_token_cost_manager"` | Passed only as an empty namespace package; no public API. |
| `python -c "import helix_token_cost_manager.inference_client"` | Failed: `ModuleNotFoundError: helix_token_cost_manager.core`. |
| `python -c "import helix_token_cost_manager.llm_agent_engine"` | Passed only because Python found `apps` in a separate local `C:\Users\Andrew\Helix\helix-unified` checkout. |
| `python -m pip install --dry-run .` | Failed: no distribution for `helix-hub-shared>=0.1.0`. |
| `python -m pytest -q` | Failed: no tests ran. |
| `python -m build` | Exited 0 with warnings, but the wheel contained only `.dist-info` metadata and `LICENSE`; no importable package code. |
| `python -m black --check .` | Failed; both source files would be reformatted and the installed Black/Python target safety check warned. |
| `python -m flake8 .` | Failed with extensive line-length/style errors. |
| `python -m mypy helix_token_cost_manager` | Produced no result for several minutes while following the external `apps.backend` graph; terminated after process inspection. |

## Prioritized findings

### P0 — release/primary journey blockers

- [x] Installation depended on unavailable `helix-hub-shared`.
- [x] Built wheel contained no package code.
- [x] Public package had no API or initializer.
- [x] Source depended on missing/private `helix-unified` modules.
- [x] No token cost calculation, usage recording, persistence, CLI, or example existed.
- [x] No tests or CI protected any journey.
- [x] README setup and links were nonfunctional and materially misleading.

### P1 — serious usefulness, reliability, security, or maintainability gaps

- [x] Remove unrelated runtime dependency sprawl (web server, provider SDKs, PostgreSQL, Redis, Celery, Discord, pandas/numpy, and more).
- [x] Use exact decimal arithmetic and effective-dated price snapshots.
- [x] Add idempotency and conflicting-request protection.
- [x] Validate/bound text, timestamps, rates, and token counts.
- [x] Add empty, success, expected-error, duplicate, and budget-denial states.
- [x] Add WAL, busy timeout, thread serialization, and newer-schema refusal.
- [x] Eliminate network, secret, prompt-logging, and unbounded API-cost paths from the product.
- [x] Replace misleading MIT/production-ready claims and the mismatched customized BSL with standard Apache-2.0 terms owned and attributed to Samsarix LLC.
- [x] Add build/install shape verification and accurate contributor guidance.
- [x] Remove the vulnerable pytest 8 development pin by setting the supported floor to maintained Python 3.10+ and pytest 9.0.3.
- [x] Upgrade the isolated build backend to setuptools 83.0.0 to close its source-distribution exclusion bypass advisory.
- [x] Replace unsafe live-WAL copy guidance with SQLite's online backup API or closed-connection copying.
- [x] Pin third-party CI actions to reviewed release commit hashes.
- [x] Reject terminal control/formatting characters and harden large Decimal aggregates and final-calendar-period bounds.
- [x] Record Samsarix LLC ownership/contact metadata and include `LICENSE`, `NOTICE`, `CITATION.cff`, and trademark guidance in the source distribution.
- [x] Run the committed CI workflow on GitHub-hosted Linux, Windows, and macOS runners.

### P2 — valuable post-`0.1` work

1. Provider response adapters that consume usage objects without importing provider SDKs.
2. Atomic JSONL/CSV import and safe export with dry-run/error ledgers.
3. Tiered/context-length and additional mutually exclusive usage types.
4. Multi-currency support with explicit exchange-rate snapshots.
5. Database backup/restore and migration tooling when schema `2` is needed.
6. Optional retention/archival commands for high-volume local stores.
7. Invoice-reconciliation samples and adapter conformance fixtures.
8. A cross-platform, hash-locked contributor dependency set and immutable CI runner images if stronger build reproducibility becomes necessary.

## Implementation checklist

- [x] One PEP 621 package definition and console script.
- [x] Zero runtime dependencies and pinned direct contributor tooling.
- [x] Validated immutable value objects and expected exception hierarchy.
- [x] Effective-dated exact pricing and fail-closed lookup.
- [x] SQLite schema `1`, WAL mode, indexes, and permission hardening.
- [x] Idempotent event recording with pricing snapshots.
- [x] Streaming exact reports and UTC filters/groups.
- [x] Global/project daily/monthly budget checks.
- [x] Human and stable JSON CLI output with meaningful exit codes.
- [x] API, CLI, concurrency, failure, empty-state, and validation tests.
- [x] Cross-platform CI and distribution smoke-test workflow.
- [x] README, contributing, changelog, security, example, and this record.
- [ ] Owner release gates listed below.

## Release acceptance criteria

- Fresh isolated installation succeeds without access to any sibling/private repository.
- Wheel contains and imports the package and exposes `samsarix-cost --version`.
- The documented price → record → report → budget journey reproduces exactly.
- Duplicate request retries are idempotent and conflicting reuse fails.
- Missing prices and newer schemas fail closed with actionable errors.
- Lint, format, type check, tests with at least 90% branch-aware coverage, build, metadata check, wheel install, and example all pass.
- No runtime dependency, secret requirement, external endpoint, network call, or locally actionable P0 remains.
- Documentation describes implemented behavior and current maturity only.
- Owner approves the version/tag/publication decision.

## Completed work

The repository now implements the chosen vertical slice end to end: local setup, explicit first price, usage estimation, persistent/idempotent recording, empty and error handling, exact reports, pre-call budgets, programmatic output, tests, packaging, CI, and accurate user/security/release documentation. The previous provider/agent extraction remains available in Git history but is no longer shipped.

## Deferred and blocked work

### Owner-, credential-, or production-blocked

- **Release identity:** owner chooses whether `0.1.0` is the initial public tag.
- **PyPI publication:** owner must control the PyPI organization/account, trusted publisher, and release approval. No credentials or external account were created.

There are no required provider credentials, production endpoints, databases, domains, or Samsarix services.

## Final verification evidence

Local verification was run on Windows from the final candidate worktree. Generated environments and artifacts were excluded from source control and removed after inspection. The pushed branch and draft pull request also ran the committed hosted matrix.

| Gate | Result |
|---|---|
| Python 3.11 lint/format/type checks | Ruff lint and format checks passed; strict mypy passed for all six package modules. |
| Python 3.11 tests | `50 passed, 1 skipped`; the skip is the POSIX-permission assertion on Windows; branch-aware coverage `94.19%` (required `90%`). |
| Python 3.13 tests and quality | `50 passed, 1 skipped`; coverage `94.65%`; Ruff, strict mypy, and `pip check` passed. |
| Python 3.10 dependency resolution | `pip install --dry-run --python-version 3.10` resolved the declared direct tool versions. No local Python 3.10 interpreter was available. |
| Build | `python -m build` produced the sdist and `py3-none-any` wheel using isolated setuptools 83.0.0; the wheel was built from the sdist. |
| Metadata and content | `twine check` passed both artifacts. The wheel contained all six renamed modules, the Samsarix console entry point, Apache license and notice, and metadata; it declared Python `>=3.10` and no runtime dependencies. The sdist also contained the citation, trademark, security, productization, example, and test files. |
| Installed journey | A fresh virtual environment installed the wheel with `--no-deps`; `pip check`, both CLI entry points, package metadata/import, price setup, a `$7.525` usage record/report, budget denial exit `3`, and `examples/quickstart.py` all passed from outside the source directory. |
| Security hygiene | Bandit, credential-pattern, and local-link checks passed. `pip-audit 2.10.0` found no known advisories in the exact direct development pins; pytest and setuptools advisories discovered during review were remediated. The transitive contributor graph was not independently audited to completion or hash-locked, so no stronger claim is made. |
| Hosted matrix | [GitHub Actions run 30392946064](https://github.com/Deathcharge/samsarix-token-cost-manager/actions/runs/30392946064) passed Python 3.10, 3.11, and 3.14 on Linux; Python 3.11 on Windows and macOS; and installed-package verification. |

## Known risks

- Stale or incorrect operator pricing produces incorrect but internally consistent cost records; explicit versioning and fail-closed exact matching make that risk visible but cannot remove it.
- Check-then-spend is not an atomic distributed reservation. Parallel callers can collectively cross a budget between checks.
- Anyone with write access to the SQLite files can tamper with accounting data; the package does not provide signatures or an append-only external audit log.
- Labels can contain sensitive business identifiers if callers misuse them.
- SQLite durability and concurrency guarantees depend on a supported local filesystem and a consistent backup made with closed connections or SQLite's online backup facilities.
- Direct contributor tools are pinned, but transitive tools and hosted runner images are not hash-locked; this limits build reproducibility and is tracked as post-`0.1` hardening.

## Security, privacy, reliability, and operating cost

The product has no server attack surface, provider/network request path, secret handling, telemetry, or runtime third-party dependency. SQL parameters and fixed grouping choices prevent straightforward injection; scalar limits bound individual inputs, streaming summaries avoid loading the entire event set, and schema and price misses fail closed. Retained history can still grow disk use, scan time, and group cardinality. Events contain accounting metadata only. Database and WAL files still require OS-level protection and consistent backups.

Software operating cost is effectively zero beyond local disk/CPU. The package does not incur model/API calls. The user's external LLM cost is computed transparently as:

`input_tokens × input_rate / 1,000,000 + output_tokens × output_rate / 1,000,000 + cached_input_tokens × cached_rate / 1,000,000`

## Distribution and sustainability model

The smallest distribution is a pure-Python wheel and source distribution on PyPI, plus direct installation from a tagged GitHub release. A plausible sustainability model is Samsarix-funded maintenance, sponsorship, or paid integration/support while keeping the library itself Apache-2.0. No willingness-to-pay or revenue viability has been validated, and the local package itself has no hosted marginal cost.

## Release disposition

**Engineering acceptance passed; public release awaits owner-controlled publication gates.** The complete primary journey, tests, security hardening, artifact build/install, local start paths, and hosted CI matrix passed. The license/ownership blocker is resolved through Samsarix LLC metadata and Apache-2.0. Publication still requires version/tag approval and authorized PyPI publication.
