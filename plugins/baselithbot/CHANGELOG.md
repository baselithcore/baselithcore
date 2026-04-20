# Changelog

All notable changes to the Baselithbot plugin are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the plugin adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Conventional Commits drive automated releases via the root
[`semantic-release`](../../.github/workflows/ci.yml) pipeline. Breaking
changes must be tagged `BREAKING CHANGE:` in the commit footer.

## [Unreleased]

### Added

- Release hygiene scaffolding: `LICENSE`, `requirements.txt`,
  `pyproject.toml`, `CHANGELOG.md`, `SECURITY.md`, `CONTRIBUTING.md`.
- `baselithbot` added to the official plugin mypy allowlist
  (`scripts/check_official_plugin_typing.py`).
- Minimal smoke test suite under `tests/plugins/baselithbot/` covering
  manifest invariants, `ApprovalGate`, secret redaction, Pydantic models
  and plugin import surface.
- Plugin-scoped CI at
  [`.github/workflows/ci.yml`](./.github/workflows/ci.yml) (ruff, mypy,
  pytest, package build). Dormant in the monorepo; activates on
  standalone extraction. Monorepo coverage remains via root
  `python_test` and `type_check_plugins` jobs.

### Changed

- `manifest.yaml` declares `id`, `entry_point`, `min_core_version`,
  `repository`, `homepage`, `icon`. License aligned to `AGPL-3.0-only`
  (matches the copyleft obligation of importing `core.*`).
- Readiness bumped from `alpha` to `beta`.
- `scripts/check_official_plugin_typing.py` no longer uses
  `--warn-unused-ignores` (conflicts with `--ignore-missing-imports` and
  generated false positives on optional-dep guards).

## [1.0.0] — 2026-04-17

### Added

- Initial plugin implementation with OpenClaw-style skills framework.
- Multi-channel adapters (Slack, Discord, Telegram, WhatsApp, IRC, Matrix,
  Web, Voice, CLI).
- Canvas / A2UI surface with live rendering.
- Desktop control lane with `pyautogui` + `mss` vision failover.
- Playwright stealth browsing with sanitized JS execution pipeline.
- Cron scheduling (native + custom) with run tracker + replay store.
- Provider secret store (Fernet-encrypted) and approval gate.
- Session manager + inbound dispatcher + node pairing policy.
- MCP tool collection and OpenClaw tool registry.
- React + Vite dashboard under `ui/` with 15+ pages.
- Observability: Prometheus metrics, OpenTelemetry tracing, usage ledger.
