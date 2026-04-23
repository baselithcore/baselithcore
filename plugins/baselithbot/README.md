# Baselithbot

> Autonomous multi-channel agent plugin for [BaselithCore](https://github.com/baselithcore/baselithcore).

[![CI](https://github.com/baselithcore/plugin-baselithbot/actions/workflows/ci.yml/badge.svg)](https://github.com/baselithcore/plugin-baselithbot/actions/workflows/ci.yml)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0--only-blue.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue.svg)](./pyproject.toml)
[![Readiness](https://img.shields.io/badge/readiness-beta-yellow.svg)](./manifest.yaml)

Baselithbot composes Playwright stealth browsing, OS-level computer
use, OpenClaw-style skills, a live Canvas (A2UI) surface, cron
scheduling, multi-channel chat adapters, voice, an MCP tool registry,
and a React dashboard into a single production-grade BaselithCore
plugin. It respects the Sacred Core rule: all domain logic lives under
`plugins/`, never inside `core/`.

---

## At a glance

| Aspect          | Value                                                                                        |
| --------------- | -------------------------------------------------------------------------------------------- |
| Version         | 1.0.0 (beta)                                                                                 |
| Min core        | `baselith-core >= 0.7.0, < 1.0.0`                                                            |
| License         | `AGPL-3.0-only`                                                                              |
| Python          | 3.10 – 3.12                                                                                  |
| Entry point     | `plugin:BaselithbotPlugin`                                                                   |
| HTTP routes     | 95 under `/baselithbot` (REST + SSE)                                                         |
| Dashboard       | 20-tab React + Vite bundle, served from `ui/dist/`                                           |
| Tests           | 262 (unit + integration); `@pytest.mark.slow` nightly                                        |
| Wheel size      | ≈ 644 KB / 204 files                                                                         |
| Channels        | 24 (Slack, Discord, Telegram, WhatsApp, Matrix, Signal, iMessage, WebChat, …)                |
| MCP tools       | 37+ (browser, computer-use, OpenClaw, set-of-mark, extras)                                   |

---

## Features

- **Stealth browsing.** `navigator.webdriver` masking, WebGL / Canvas
  fingerprint perturbation, Accept-Language spoofing, rotating user
  agent pool.
- **Observe → Plan → Act agent loop.** Vision-driven planner with
  Set-of-Mark DOM overlays so the VLM reasons by element index.
- **Sanitized JavaScript execution.** `eval_js_safe` MCP tool
  constrained to a whitelist; arguments pass through
  `core.services.sanitization.InputSanitizer`.
- **Computer-Use safety model.** Anthropic-style master switch +
  per-capability allow flags (mouse / keyboard / screenshot / shell /
  filesystem). Shell allowlist with `shell=False`. Filesystem scoped
  under a resolved root (no `..` traversal).
- **Human-in-the-loop approvals.** Every privileged action parks in
  `ApprovalGate` until a dashboard operator approves / denies. Timeout
  auto-denies and audits.
- **Time-travel replay.** Each Observe → Plan → Act step persists to
  SQLite (`replay.sqlite`) with screenshot + reasoning. 14-day
  retention via cron.
- **Encrypted provider keys.** Fernet-encrypted
  `provider_keys.enc.json` with auto-generated `.secret_key`; the
  dashboard shows only `***<last4>` previews.
- **Live canvas (A2UI).** Server-pushed widget graph (`Text`,
  `Button`, `Image`, `List`) rendered in the dashboard. Bidirectional
  event round-trip.
- **24 chat channels.** First-party Slack / Discord / Telegram /
  WhatsApp / Matrix / iMessage / WebChat, plus Feishu / LINE /
  Mattermost / Signal / Nostr / Tlon / Twitch / Zalo / WeChat / QQ /
  Microsoft Teams / Google Chat / Nextcloud Talk / BlueBubbles /
  Synology Chat / IRC.
- **Native + custom cron jobs** with per-run tracking and replay
  correlation.
- **Observability.** Prometheus metrics, OpenTelemetry tracing, per-run
  usage / cost ledger, structured `structlog` output.

---

## Repository layout

```text
plugin-baselithbot/
├── manifest.yaml              # Marketplace metadata (id, entry_point, …)
├── catalog-info.yaml          # Backstage catalog entry
├── pyproject.toml             # PEP-621 package metadata
├── requirements.txt           # Runtime pins (mirrors python_dependencies)
├── README.md  LICENSE  CHANGELOG.md  CONTRIBUTING.md
├── CODE_OF_CONDUCT.md  SECURITY.md  DOCUMENTATION.md
├── assets/
│   └── logobg-baselithbot500.png
├── docs/                      # Full operator + developer docs
├── ui/                        # React + Vite dashboard (ships as ui/dist)
├── dashboard/                 # Backend REST + SSE routes
│
├── plugin.py                  # BaselithbotPlugin entry point
├── types.py                   # Shared Pydantic models
├── _bootstrap.py  _mcp.py     # Internal init helpers
│
├── api/                       # HTTP surface
│   ├── router.py              # POST /run · GET /status
│   ├── handlers.py            # Orchestrator flow handlers
│   └── ui_api.py              # Back-compat shim
├── browser/                   # Browser subsystem
│   ├── agent.py               # BaselithbotAgent (Observe→Plan→Act loop)
│   ├── stealth.py  som.py
│   ├── tools.py               # 7 browser MCP tools
│   └── js_whitelist.py  http_pool.py  vision_failover.py  web_launcher.py
├── computer_use/              # OS-level control
│   ├── config.py              # ComputerUseConfig · AuditLogger
│   ├── tools.py               # 12 Computer-Use MCP tools
│   ├── os_control.py  desktop_vision.py  desktop_lane.py
│   ├── shell_exec.py  filesystem.py  process_manager.py
│   └── spotify_control.py  extra_tools.py
├── control/                   # Safety, replay, OpenClaw MCP tools
│   ├── approvals.py           # HITL ApprovalGate
│   ├── replay.py              # TaskReplayStore (SQLite)
│   ├── run_tracker.py
│   └── openclaw_tools.py      # 17 OpenClaw-parity MCP tools
├── cron/                      # Scheduling
│   ├── scheduler.py  custom.py
├── chat/                      # Slash / chat commands
│   ├── commands.py  slash_defaults.py
├── config/                    # Runtime config overlay + model selection
│   ├── runtime.py  models.py
├── security/                  # Secret store + redaction
│   ├── secret_store.py  redaction.py
├── observability/             # Metrics, tracing, usage ledger
│   ├── metrics.py  tracing.py  usage.py  hooks.py
├── diagnostics/               # CLI, doctor, environment probes
│   ├── cli.py  doctor.py  ollama_probe.py
│
├── agents/   canvas/   channels/   code_edit/   deploy/
├── desktop_agent/   gateway/   inbound/   integrations/
├── model_routing/   nodes/   policies/   sessions/
├── skills/   voice/   workspace/
│
└── .github/                   # Workflows, issue templates, dependabot
```

Every top-level directory under the repo root is a declared Python
subpackage (see `[tool.setuptools] packages` in
[`pyproject.toml`](./pyproject.toml)). The installed module name is
`baselithbot` via `package-dir = { "baselithbot" = "." }`.

---

## Installation

From the marketplace wheel (once published):

```bash
pip install baselithcore-baselithbot-plugin
playwright install chromium
```

From source (editable):

```bash
git clone https://github.com/baselithcore/plugin-baselithbot
cd plugin-baselithbot
pip install -e ".[dev]"
playwright install chromium
```

Pre-build the dashboard bundle (ships as `ui/dist/`):

```bash
cd ui && npm ci && npm run build
```

---

## Quick start

### REST

```bash
curl -X POST http://localhost:8000/baselithbot/run \
  -H "Content-Type: application/json" \
  -d '{"goal": "search anthropic on duckduckgo and report top result",
       "start_url": "https://duckduckgo.com"}'
```

### CLI

```bash
baselith baselithbot run "open hacker news and list top 3 stories"
baselith baselithbot onboard --write                 # configs/plugins.yaml wizard
baselith baselithbot onboard --install-daemon        # launchd / systemd user unit
baselith baselithbot gateway --host 0.0.0.0 --port 18789
baselith baselithbot pairing approve slack U12345ABC
```

### Python SDK

```python
from plugins.baselithbot import BaselithbotAgent, BaselithbotTask

agent = BaselithbotAgent(config={"headless": True})
await agent.startup()
result = await agent.execute(BaselithbotTask(goal="search baselithcore"))
await agent.shutdown()
```

---

## Dashboard

A self-contained React + Vite SPA served from `/baselithbot/ui`. Twenty
pages cover: Overview · RunTask · Sessions · Channels · Skills ·
Crons · Nodes · Workspaces · Agents · Canvas · Models · Metrics ·
Logs · Doctor · ComputerUse · Stealth · AuditLog · Approvals ·
Replay · NotFound.

Stack: React 18, Vite 5, TypeScript, TanStack Query, React Router,
Server-Sent Events, Chart.js via `react-chartjs-2`, vanilla CSS
(no Tailwind).

Dev server with API proxy to `:8000`:

```bash
cd ui && npm run dev     # http://localhost:5180
```

Production build (`ui/dist/`) is bundled in the Python wheel via
`[tool.setuptools.package-data]`.

---

## Configuration

```yaml
baselithbot:
  enabled: true
  headless: true
  max_steps: 20
  viewport_width: 1280
  viewport_height: 720
  stealth:
    enabled: true
    rotate_user_agent: true
    mask_webdriver: true
    spoof_languages: ["en-US", "en"]
    spoof_timezone: "UTC"
  computer_use:
    enabled: false                    # opt-in
    allow_shell: false
    allow_filesystem: false
    allowed_shell_commands: ["ls", "pwd", "git status"]
    filesystem_root: "/var/lib/baselithbot/workspace"
    audit_log_path: "/var/log/baselithbot/computer_use.jsonl"
    require_approval_for: ["shell", "filesystem"]
    approval_timeout_seconds: 120
```

`computer_use` and `stealth` fields mutate live from the dashboard
(`PUT /baselithbot/dash/computer-use`, `PUT /baselithbot/dash/stealth`).
Overrides persist to `.state/runtime_config.json` and invalidate the
cached agent so the next run rebuilds with the new policy.

### Environment variables

| Variable                                | Purpose                                                                 |
| --------------------------------------- | ----------------------------------------------------------------------- |
| `BASELITHBOT_DASHBOARD_TOKEN`           | Bearer token required on every dashboard write endpoint.                |
| `BASELITHBOT_DASHBOARD_ALLOW_INSECURE`  | `1` to open writes without a token (local dev only — logs a warning).   |
| `BASELITHBOT_SECRET_KEY`                | Fernet master for the provider-key store (auto-generated if unset).     |

---

## Security model

- **Fail-closed dashboard writes.** `503` without a configured token
  and no insecure flag.
- **Subprocess hardening.** Every shell invocation uses `shell=False`
  with an explicit argv vector; the first token is matched against
  `allowed_shell_commands`. Per-call timeout.
- **Filesystem scoping.** `Path.resolve()` + containment check on
  every read / write / list. Size cap via `filesystem_max_bytes`.
- **HITL approvals.** `require_approval_for` parks privileged actions
  in `ApprovalGate` until an operator acts or the timeout fires.
- **Audit log.** JSON Lines at `audit_log_path` + structured log
  emission for every privileged action.
- **Replay.** Each step persists to SQLite for post-hoc review
  (14-day retention).
- **Encrypted secrets.** Fernet-encrypted provider keys at rest;
  plaintext never leaves the process.

Threat model: [`docs/security.md`](./docs/security.md).

---

## Subsystems deep-dive

Full operator + developer documentation lives under
[`docs/`](./docs/):

| Doc                                              | Topic                                                    |
| ------------------------------------------------ | -------------------------------------------------------- |
| [`docs/architecture.md`](./docs/architecture.md) | Observe → Plan → Act loop, subsystem layering            |
| [`docs/configuration.md`](./docs/configuration.md) | Every setting, every override, every env var           |
| [`docs/computer-use.md`](./docs/computer-use.md) | Anthropic Computer-Use safety model                      |
| [`docs/approvals.md`](./docs/approvals.md)       | HITL `ApprovalGate` workflow                             |
| [`docs/replay.md`](./docs/replay.md)             | Time-travel replay, retention, scrubber UI               |
| [`docs/skills.md`](./docs/skills.md)             | Bundled / managed / workspace skill scopes               |
| [`docs/dashboard.md`](./docs/dashboard.md)       | REST + SSE endpoints and UI pages                        |
| [`docs/mcp-tools.md`](./docs/mcp-tools.md)       | 37+ MCP tools reference                                  |
| [`docs/cli-sdk.md`](./docs/cli-sdk.md)           | `baselith baselithbot` subcommand surface                |
| [`docs/http-api.md`](./docs/http-api.md)         | Request / response schemas                               |
| [`docs/observability.md`](./docs/observability.md) | Prometheus, OpenTelemetry, usage ledger                |
| [`docs/security.md`](./docs/security.md)         | Threat model + hardening                                 |
| [`docs/operations.md`](./docs/operations.md)     | Deployment, upgrades, disaster recovery                  |
| [`docs/publishing.md`](./docs/publishing.md)     | Marketplace submission flow                              |

---

## Development

```bash
# Install dev deps
pip install -e ".[dev]"
pre-commit install

# Fast inner loop
ruff check . --exclude ui,dashboard,docs,.state,tests
mypy . --explicit-package-bases --ignore-missing-imports --follow-imports=skip *.py \
  agents api browser canvas channels chat code_edit computer_use config \
  control cron deploy desktop_agent diagnostics gateway inbound \
  integrations model_routing nodes observability policies security \
  sessions skills voice workspace

# Unit + smoke tests
pytest -v --no-cov
pytest -m slow --no-cov            # @pytest.mark.slow nightly suite

# Dashboard hot-reload
( cd ui && npm run dev )
```

The plugin participates in the BaselithCore monorepo CI:

- [`scripts/check_architecture_boundaries.py`](https://github.com/baselithcore/baselithcore/blob/main/scripts/check_architecture_boundaries.py)
  enforces the Sacred Core rule (no `core → plugins` imports).
- [`scripts/check_official_plugin_typing.py`](https://github.com/baselithcore/baselithcore/blob/main/scripts/check_official_plugin_typing.py)
  runs `mypy` on every official plugin (allowlist includes this one).
- Plugin-scoped `.github/workflows/ci.yml` runs ruff, mypy, pytest,
  and `pip wheel` on every push / PR once this tree is extracted as
  a standalone repo.

---

## Repository model

Baselithbot is **dual-hosted, single-sourced**:

- **Source of truth**:
  [`plugins/baselithbot/`](https://github.com/baselithcore/baselithcore/tree/main/plugins/baselithbot)
  inside the `baselithcore` monorepo. All PRs, fixes, and feature work
  land **here first**.
- **Publish target**:
  [`plugin-baselithbot`](https://github.com/baselithcore/plugin-baselithbot)
  (this repository). Populated via `git subtree split` from the
  monorepo on every release — **never edit directly**. Any commit
  landing outside the subtree push will be force-overwritten on the
  next release.

Issue triage happens here (marketplace consumers find it first); fixes
merge into the monorepo and re-propagate via subtree. Full flow in
[`docs/publishing.md`](./docs/publishing.md) §8.

---

## Marketplace publication

Baselithbot is published to the
[Baselith Marketplace](https://marketplace.baselithcore.xyz/) via the
Backstage Scaffolder or the `baselith marketplace` CLI. Full checklist,
validator compliance, and release cadence in
[`docs/publishing.md`](./docs/publishing.md).

---

## Contributing

See [`CONTRIBUTING.md`](./CONTRIBUTING.md). All PRs land in the
monorepo source of truth — not on this repository directly. Bug
reports and security disclosures go through
[`SECURITY.md`](./SECURITY.md).

## License

[AGPL-3.0-only](./LICENSE) — matches the copyleft obligation of
importing `core.*` from `baselith-core`.
