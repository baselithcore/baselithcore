# Contributing to Baselithbot

Thanks for wanting to improve Baselithbot. The plugin is **dual-hosted
but single-sourced**:

- **Source of truth**: `plugins/baselithbot/` inside the
  [`baselithcore`](https://github.com/baselithcore/baselithcore)
  monorepo. All PRs, fixes, and feature work land **here first**.
- **Publish target**: the standalone
  [`plugin-baselithbot`](https://github.com/baselithcore/plugin-baselithbot)
  repo is populated via `git subtree split` from the monorepo and is
  **output-only** — it is force-pushed on each release. Never send a PR
  directly to it; any commit landing outside the subtree push will be
  overwritten on the next release.

Issues raised on the standalone repo are triaged there (marketplace
consumers find it first) but the fix merges into `baselithcore`
and re-propagates via subtree. See
[`docs/publishing.md`](./docs/publishing.md) §8 for the full release
flow.

## Ground Rules

1. **Sacred Core invariant.** Baselithbot is a plugin — never import
   `plugins/baselithbot/*` from `core/`. If you need a new capability in
   `core/`, raise it as a separate PR first.
2. **Conventional Commits.** Commit messages drive automated releases via
   `semantic-release`. Use `feat:`, `fix:`, `refactor:`, `docs:`, `test:`,
   `chore:`, `perf:`, `build:`, `ci:`. Breaking changes go in a
   `BREAKING CHANGE:` footer.
3. **500-line file cap.** Split modules that grow past that.
4. **Strict typing.** Baselithbot is in the official plugin mypy allowlist
   (`scripts/check_official_plugin_typing.py`) — new code must type-check.
5. **Tests required for behavior changes.** Bug fix or feature → new test.
   Refactors can piggy-back on existing coverage.

## Local Development

```bash
pip install -e ".[dev]"
pre-commit install

# Fast inner loop
ruff check plugins/baselithbot
mypy --config-file plugins/baselithbot/pyproject.toml plugins/baselithbot
pytest tests/plugins/baselithbot -v

# Full official-plugin typing gate (matches CI)
python scripts/check_official_plugin_typing.py

# Architecture guard
python scripts/check_architecture_boundaries.py
```

Dashboard (React + Vite) development:

```bash
cd plugins/baselithbot/ui
npm install
npm run dev
```

## PR Checklist

- [ ] `ruff check` + `ruff format --check` clean.
- [ ] `mypy` clean under the official-plugin gate.
- [ ] `pytest tests/plugins/baselithbot` green.
- [ ] `CHANGELOG.md` updated under `## [Unreleased]` when user-visible.
- [ ] `docs/` updated when behavior or configuration changes.
- [ ] Conventional Commits in the PR title.
- [ ] No secrets, tokens, or real endpoints committed.

## Security-sensitive Changes

Changes to any of these require a second reviewer and updated threat notes
in [`SECURITY.md`](./SECURITY.md):

- `approvals.py`, `secret_store.py`, `secret_redaction.py`
- `shell_exec.py`, `computer_tools.py`, `os_control.py`
- `desktop_agent/`, `desktop_lane.py`
- `policies/`, `gateway/`
- Anything that touches inbound handling (`inbound/`) or the dashboard
  auth surface.

Report suspected vulnerabilities privately — see
[`SECURITY.md`](./SECURITY.md) for the disclosure channel.

## Release Flow

1. Merge conventional commits to `main`.
2. `semantic-release` (root CI) computes the next version and tags it.
3. The marketplace workflow (`baselith-plugin-publish` Backstage template)
   produces the ZIP and pushes to
   [Baselith Marketplace](https://marketplace.baselithcore.xyz).
4. CHANGELOG.md is regenerated from commit history.

Manual release (escape hatch) is documented in
[`docs/publishing.md`](./docs/publishing.md).
