# Release checklist

This is an owner-controlled checklist for the first public Samsarix Token Cost Manager release. It documents the remaining gates; it does not authorize a tag or package upload.

## Current pre-release state

As of August 11, 2026:

- `main` is public and Apache-2.0 licensed;
- feature-complete candidate `2a53ca7` passed exact-head cross-platform CI and installed-wheel verification;
- no Git tag or GitHub Release exists;
- the PyPI JSON endpoint for `samsarix-token-cost-manager` returns `404`, which is not a name reservation;
- no PyPI trusted publisher or release workflow is configured; and
- no GitHub default-branch protection/ruleset is configured.

## One-time owner setup

1. Confirm Samsarix LLC owns or is authorized to publish the package name, version, marks, source, and Apache-2.0 notices.
2. Create or control the PyPI project/organization with MFA-protected owner accounts.
3. Add a narrowly scoped GitHub Actions release workflow and environment, pin its third-party actions to reviewed commits, and give only its publish job `id-token: write`.
4. Configure that exact repository/workflow/environment as a [PyPI Trusted Publisher](https://docs.pypi.org/trusted-publishers/). Trusted Publishing uses short-lived OIDC credentials instead of a long-lived workstation token.
5. Add a default-branch ruleset that requires the committed CI checks and preserves a documented administrator recovery path. Test the rule on a non-release change before relying on it.
6. Validate the full workflow against TestPyPI or a non-publishing build mode before creating the public tag.

The release workflow is intentionally not preconfigured in this repository because its trusted-publisher environment and approval policy must match owner-controlled PyPI state.

## Per-release gate

1. Start from a clean, reviewed `main` commit whose exact-head CI is green.
2. Choose the SemVer version, update `__version__` and the changelog together, and verify package metadata, public API compatibility, and migrations.
3. Run Ruff, strict mypy, the full test suite, `python -m build`, `twine check`, and a fresh-wheel smoke test outside the source tree.
4. Review wheel/sdist contents, record SHA-256 digests, and confirm no databases, credentials, temporary files, or untracked source are included.
5. Create the approved signed/annotated tag and GitHub Release from that exact commit. GitHub documents the release object and asset flow in [Managing releases in a repository](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository).
6. Let the trusted workflow build once from the tag and publish those exact artifacts. Follow the [Python Packaging User Guide](https://packaging.python.org/en/latest/tutorials/packaging-projects/#uploading-the-distribution-archives); do not rebuild different bytes for PyPI and GitHub.
7. Install the published version into a fresh environment and repeat the token-price, billable-unit, ledger, report, and budget smoke paths.
8. Record the tag, commit, workflow run, artifact digests, PyPI/GitHub URLs, approver, and supported-version window in the release notes.

## Failure handling

PyPI releases are immutable. If a published artifact is wrong, stop promotion, preserve evidence, yank the affected version when appropriate, fix forward with a new version, and publish a security advisory when impact warrants it. Never delete or rewrite Git history to hide a released defect.
