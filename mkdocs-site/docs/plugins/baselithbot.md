---
title: Baselithbot
description: Autonomous multi-channel agent plugin — OpenClaw skills, stealth browsing, desktop control, canvas A2UI, voice, cron, MCP.
---

The `baselithbot` plugin is the flagship autonomous agent shipped with
BaselithCore. It composes the `browser_agent` Playwright backend with an
explicit Observe → Plan → Act cognitive loop, adds OpenClaw-style skills,
desktop-control (`pyautogui` + `mss` vision failover), a live Canvas
(A2UI) surface, multi-channel chat adapters, cron scheduling, a 20-tab
React dashboard, and an MCP tool registry.

Version **1.0.0** (beta readiness). Plugin lives at
[`plugins/baselithbot/`](https://github.com/baselithcore/plugin-baselithbot).

## Why a plugin (Sacred Core compliance)

Baselithbot lives entirely under `plugins/` and never touches `core/`.
Domain-specific concerns — stealth countermeasures, OpenClaw tool
layout, channel adapters, the React dashboard — are kept out of the
framework. `core/` stays domain-agnostic; `baselithbot` composes
primitives exposed through `core.plugins`, `core.services.vision`,
`core.observability.logging`, and the plugin registry.

## Status at release (v1.0.0)

- **95** FastAPI routes mounted under `/baselithbot/` (main router + the
  `/dash` dashboard subrouter).
- **20-tab React dashboard** under `plugins/baselithbot/ui/` — served
  from the compiled bundle in `ui/dist/`. All tab anchor GETs return
  200 against a test client.
- **74 tests** — 63 unit + 11 `@pytest.mark.slow` integration
  (cron-scheduler lifecycle, SessionManager LRU eviction, replay-store
  SQLite persistence).
- **Packaging**: wheel ≈ 644 KB, 204 files. `ui/src/`,
  `ui/node_modules/`, `__pycache__`, and `*.pyc` artifacts are excluded
  via `[tool.setuptools.exclude-package-data]`. The wheel ships only
  `ui/dist/**`, `docs/**`, `manifest.yaml`, `catalog-info.yaml`, and
  `logobg-baselithbot500.png`.
- **CI gates**: `ruff check`, `scripts/check_architecture_boundaries.py`,
  `scripts/check_official_plugin_typing.py` — all green.
- **Security**: dashboard writes are fail-closed (503 without
  `BASELITHBOT_DASHBOARD_TOKEN`). `BASELITHBOT_DASHBOARD_ALLOW_INSECURE=1`
  is dev-only and logs a warning on first use.
- **License**: `AGPL-3.0-only` (matches the copyleft obligation of
  importing `core.*`).

## Capability surface

| Subsystem                 | Module                                                      |
| ------------------------- | ----------------------------------------------------------- |
| Browser loop + stealth    | `agent.py`, `stealth.py`, `js_whitelist.py`                 |
| Desktop / Computer-Use    | `computer_use.py`, `desktop_lane.py`, `os_control.py`       |
| OpenClaw skills           | `skills/` (registry, loader, ClawHub, writer, 12 bundled)   |
| Multi-channel adapters    | `channels/` (24 adapters — Slack, Discord, Telegram, …)     |
| Sessions + inbound        | `sessions/`, `inbound/`, `policies/dm_policy.py`            |
| Canvas (A2UI)             | `canvas/`                                                   |
| Cron (native + custom)    | `cron.py`, `cron_custom.py`                                 |
| Node pairing              | `nodes/`, `policies/dm_policy.py`                           |
| Replay + audit            | `replay.py`, `run_tracker.py`                               |
| Secret store (Fernet)     | `secret_store.py`                                           |
| Approval gate             | `approvals.py`                                              |
| MCP tools                 | `_mcp.py`, `openclaw_tools.py`, `computer_tools.py`         |
| Dashboard (REST + SSE)    | `dashboard/app.py`, `dashboard/routes/**`                   |
| React UI                  | `ui/` (Vite + TypeScript, 20 pages)                         |

## Building and packaging

The React dashboard must be compiled before the Python wheel is built,
because only `ui/dist/` is bundled:

```bash
cd plugins/baselithbot/ui
npm ci
npm run build
cd -

python -m pip wheel --no-deps --no-build-isolation \
    plugins/baselithbot -w /tmp/baselithbot-wheel

# Sanity check: node_modules must not ship
python -m zipfile -l /tmp/baselithbot-wheel/*.whl | grep -c node_modules
# → 0
```

Publishing to the marketplace is covered in detail in
[`plugins/baselithbot/docs/publishing.md`](https://github.com/baselithcore/baselithcore/blob/main/plugins/baselithbot/docs/publishing.md)
(and the one-click Backstage Scaffolder path in
[Backstage Publish](backstage-publish.md)).

## Runtime configuration

Two env vars gate the dashboard API:

| Variable                               | Purpose                                                               |
| -------------------------------------- | --------------------------------------------------------------------- |
| `BASELITHBOT_DASHBOARD_TOKEN`          | Shared bearer token required on every write endpoint.                |
| `BASELITHBOT_DASHBOARD_ALLOW_INSECURE` | `1` to open writes without a token (local dev only — logs warning).  |

Provider secrets live in `plugins/baselithbot/.state/provider_keys.enc.json`
(Fernet-encrypted; the `.secret_key` next to it is auto-generated on
first boot and git-ignored). The dashboard never echoes plaintext —
only `***<last4>` previews.

## Repository model

Baselithbot is **dual-hosted** but single-sourced:

- **Source of truth** — `plugins/baselithbot/` inside the `baselithcore`
  monorepo. All edits, bug fixes, and feature work land here first. The
  framework CI gates allowlist the plugin:
  [`scripts/check_official_plugin_typing.py`](https://github.com/baselithcore/baselithcore/blob/main/scripts/check_official_plugin_typing.py),
  [`scripts/check_architecture_boundaries.py`](https://github.com/baselithcore/baselithcore/blob/main/scripts/check_architecture_boundaries.py),
  plus `tests/plugins/baselithbot/` and
  `tests/unit/plugins_tests/test_baselithbot_*.py` — so every `core.*`
  change is immediately regression-tested against Baselithbot.
- **Publish target** — the standalone
  [`plugin-baselithbot`](https://github.com/baselithcore/plugin-baselithbot)
  repository. Marketplace consumers `pip install` from here. It is
  **output-only**: every release is a `git subtree split` from the
  monorepo, pushed with `--force-with-lease`. Never edit it directly.

Release flow summary:

```bash
cd baselithcore
# 1. Land changes in monorepo (PR + review as usual).
# 2. Rebuild UI bundle (ships only ui/dist/).
( cd plugins/baselithbot/ui && npm ci && npm run build )
# 3. Split and push.
git subtree split -P plugins/baselithbot -b baselithbot-split
git push --force-with-lease \
    git@github.com:baselithcore/plugin-baselithbot.git \
    baselithbot-split:main
git branch -D baselithbot-split
# 4. In the standalone repo: tag + `baselith marketplace publish .`
# (or use the Backstage Scaffolder path).
```

The full pipeline — layout checklist, validator gates, Backstage
Scaffolder path — lives in
[`plugins/baselithbot/docs/publishing.md`](https://github.com/baselithcore/baselithcore/blob/main/plugins/baselithbot/docs/publishing.md).

## Where to look next

- Plugin-local README:
  [`plugins/baselithbot/README.md`](https://github.com/baselithcore/baselithcore/blob/main/plugins/baselithbot/README.md)
- Operations + security walkthroughs:
  [`plugins/baselithbot/docs/`](https://github.com/baselithcore/baselithcore/tree/main/plugins/baselithbot/docs)
- Publishing (manual + Scaffolder):
  [Backstage Publish](backstage-publish.md)
- Packaging rules for all plugins:
  [Packaging](packaging.md)
