# Baselithbot — Technical & Operator Documentation

> Version: `1.0.0` · Readiness: `alpha` · License: MIT
> Framework: BaselithCore (Python 3.10–3.12, FastAPI, Pydantic, async I/O)
> Location: [`plugins/baselithbot/`](./)

Baselithbot is the OpenClaw-parity agentic platform shipped as a single
BaselithCore plugin. It bundles autonomous browser navigation, OS-level
Computer Use, a multi-channel inbox, a Live Canvas (A2UI) surface, voice
output, sandboxed sessions, a skills registry, a cron scheduler, node
pairing, a remote gateway, a cost/usage ledger, a React dashboard, and an
extensive MCP tool surface — all composable from one plugin mount point
(`/baselithbot`).

This document is exhaustive: it covers architecture, configuration,
deployment, every HTTP/WS endpoint, every MCP tool, the safety model, the
dashboard, and day-2 operations. Pair it with the quick-start section in
[README.md](./README.md) for copy/paste recipes.

---

## Table of contents

1. [Overview & design goals](#1-overview--design-goals)
2. [Architecture](#2-architecture)
3. [Module map](#3-module-map)
4. [Installation](#4-installation)
5. [Configuration reference](#5-configuration-reference)
6. [Lifecycle & state machine](#6-lifecycle--state-machine)
7. [HTTP API reference](#7-http-api-reference)
8. [Dashboard (React SPA)](#8-dashboard-react-spa)
9. [MCP tools (36)](#9-mcp-tools-36)
10. [Channels (24) & inbound dispatch](#10-channels-24--inbound-dispatch)
11. [Sessions & sandbox](#11-sessions--sandbox)
12. [Skills registry (ClawHub)](#12-skills-registry-clawhub)
13. [Cron scheduler](#13-cron-scheduler)
14. [Node pairing & gateway](#14-node-pairing--gateway)
15. [Voice & Canvas (A2UI)](#15-voice--canvas-a2ui)
16. [Computer Use safety model](#16-computer-use-safety-model)
17. [Model preferences (LLM/Vision failover)](#17-model-preferences-llmvision-failover)
18. [Authentication, rate limits, security headers](#18-authentication-rate-limits-security-headers)
19. [Observability (metrics, audit, events)](#19-observability-metrics-audit-events)
20. [CLI reference](#20-cli-reference)
21. [Programmatic usage (Python)](#21-programmatic-usage-python)
22. [Testing](#22-testing)
23. [Deployment recipes](#23-deployment-recipes)
24. [Troubleshooting & FAQ](#24-troubleshooting--faq)
25. [Roadmap](#25-roadmap)
26. [Marketplace publication](#26-marketplace-publication)

---

## 1. Overview & design goals

Baselithbot targets four orthogonal capabilities behind a single plugin:

| Capability | Purpose |
|------------|---------|
| Autonomous browser agent | Goal-driven Observe → Plan → Act loop over Playwright |
| OS-level Computer Use | Mouse / keyboard / screenshot / shell / filesystem primitives |
| Messaging & orchestration | 24 channel adapters, sessions, cron, pairing, skills |
| Operator control plane | Secured FastAPI + React dashboard with SSE live events |

**Design invariants (aligned with the BaselithCore Sacred Core rule):**

- Lives entirely under [`plugins/`](../). Never imports `core → plugins`.
- Composes [`plugins/browser_agent`](../browser_agent/) for Playwright; does
  not reimplement the driver layer.
- Every module ≤ 500 LOC (repository cap).
- All I/O is `async/await`; every config object is a Pydantic model.
- Opt-in privilege: Computer Use defaults to **disabled**, each
  sub-capability gates independently.
- Every privileged action is written to a JSON-Lines audit log.
- Every dashboard write endpoint is rate-limited, bearer-token-guarded,
  and logged.

---

## 2. Architecture

### 2.1 Layer cake

```text
┌────────────────────────────────────────────────────────────┐
│  React SPA (Vite)                                          │
│  ui/dist served under  /baselithbot/ui                     │
└───────────────▲────────────────────────────────────────────┘
                │ REST + SSE
┌───────────────┴────────────────────────────────────────────┐
│  FastAPI router  (plugins/baselithbot/router.py)           │
│    /baselithbot/run /status /inbound /ws/pair /metrics     │
│    /baselithbot/dash/*   (ui_api.py)                       │
│    /baselithbot/ui/*     (static bundle)                   │
└───────────────▲────────────────────────────────────────────┘
                │
┌───────────────┴────────────────────────────────────────────┐
│  BaselithbotPlugin   (plugin.py)                           │
│  holds singletons: agent, sessions, channels, skills,      │
│  cron, pairing, canvas, usage, workspaces, run_tracker,    │
│  inbound_dispatcher, dm_policy, model_prefs, slash_state   │
└──┬───────┬───────┬──────┬──────┬─────┬────────┬────────────┘
   │       │       │      │      │     │        │
   ▼       ▼       ▼      ▼      ▼     ▼        ▼
 Agent  Handlers Channels Skills Cron Nodes  ComputerUse
   │                                          │
   ▼                                          ▼
 BrowserAgent (browser_agent plugin)     OSController /
 + stealth + sanitized JS                ScopedFS / Shell /
                                         Filesystem / Audit
```

### 2.2 Observe → Plan → Act loop

[`agent.py`](./agent.py) implements the cognitive loop:

1. **Observe** — `BrowserAgent.get_page_state()` returns `(url, screenshot_base64, html_snippet)`.
2. **Plan** — `BrowserAgent.decide_next_action(goal, state, history)` returns a typed `BrowserAction` (navigate / click / type / scroll / extract / done / fail).
3. **Act** — action is dispatched via sanitized primitives; `EXTRACT` records into a per-run store; `DONE`/`FAIL` terminates; `MAX_STEPS` returns partial result.

Per step the agent emits a `on_progress` callback consumed by
[`run_tracker`](./run_tracker.py) and the `DashboardEventBus` so the UI can
render real-time step reasoning, screenshots, and extracted data.

### 2.3 Plugin registration

`BaselithbotPlugin` subclasses both `AgentPlugin` and `RouterPlugin`
([`core/plugins`](../../core/plugins/)). During BaselithCore app startup,
the registry:

1. Calls `initialize(config)` with the block from `configs/plugins.yaml`.
2. Calls `create_router()` → mounted under **`/baselithbot`** (not
   `/api/baselithbot`, override via `get_router_prefix`) so the UI is
   reachable at a human-friendly URL.
3. Merges `get_mcp_tools()` into the MCP server.
4. Registers `get_intent_patterns()` — the `baselithbot_browse` intent
   (priority 110) routes matching user utterances to `handle_browse`.
5. Calls `shutdown()` on app teardown — stops the agent, cron scheduler,
   and every live channel.

The singleton browser agent is lazily started on first use via
`get_or_start_agent()` to avoid launching Chromium on unused deploys.

---

## 3. Module map

### 3.1 Browser layer

| File | Role |
|------|------|
| [`plugin.py`](./plugin.py) | Registration entry point; holds singletons |
| [`agent.py`](./agent.py) | `BaselithbotAgent` cognitive loop over BrowserAgent |
| [`handlers.py`](./handlers.py) | `BaselithbotFlowHandler.handle_browse` intent bridge |
| [`router.py`](./router.py) | FastAPI router (`/run`, `/status`, `/inbound`, `/ws/pair`, `/metrics`, UI mount) |
| [`tools.py`](./tools.py) | 7 browser MCP tools |
| [`types.py`](./types.py) | `StealthConfig`, `BaselithbotTask`, `BaselithbotResult` |
| [`stealth.py`](./stealth.py) | WebDriver mask + UA rotation + locale spoof |
| [`js_whitelist.py`](./js_whitelist.py) | `ALLOWED_SNIPPETS` for `eval_js_safe` |
| [`cli.py`](./cli.py) | `baselith baselithbot {run, status, onboard, pairing, gateway}` |

### 3.2 Computer Use layer

| File | Role |
|------|------|
| [`computer_use.py`](./computer_use.py) | `ComputerUseConfig`, `AuditLogger`, `ComputerUseError` |
| [`os_control.py`](./os_control.py) | Mouse/keyboard via `pyautogui` |
| [`desktop_vision.py`](./desktop_vision.py) | Screenshots via `mss` + `Pillow` |
| [`shell_exec.py`](./shell_exec.py) | Allowlisted subprocess (`shell=False`, timeout) |
| [`filesystem.py`](./filesystem.py) | `ScopedFileSystem` — `..`-blocked, byte-cap |
| [`process_manager.py`](./process_manager.py) | `psutil`-based process inspection |
| [`computer_tools.py`](./computer_tools.py) | 12 Computer Use MCP tools |
| [`secret_redaction.py`](./secret_redaction.py) | Scrubs tokens/keys from audit log |

### 3.3 OpenClaw-parity layer

| Subsystem | Files |
|-----------|-------|
| Channels | [`channels/`](./channels/) (24 adapters + base + registry + bootstrap) |
| Voice / Audio | `voice/` (system TTS + ElevenLabs HTTP + wake state) |
| Canvas / A2UI | `canvas/` (`CanvasSurface`, widgets, `A2UIRenderer`) |
| Sessions | [`sessions/`](./sessions/) (`SessionManager`, `DockerSandbox`) |
| Skills | [`skills/`](./skills/) (`SkillRegistry`, ClawHub loader) |
| Nodes | [`nodes/`](./nodes/) (`NodePairing`, WS tokens, command families) |
| Gateway | [`gateway/`](./gateway/) (SSH + Tailscale + provisioning) |
| Integrations | [`integrations/`](./integrations/) (webhooks, Gmail Pub/Sub) |
| Cron | [`cron.py`](./cron.py) (`CronScheduler` async loop) |
| Chat commands | [`chat_commands.py`](./chat_commands.py) (10 slash commands) |
| Doctor | [`doctor.py`](./doctor.py) (dep/env probe) |
| Inbound | [`inbound/`](./inbound/) (payload parsers + dispatcher) |

### 3.4 Control plane

| File | Role |
|------|------|
| [`ui_api.py`](./ui_api.py) | Dashboard REST + SSE router (`/baselithbot/dash/*`) |
| [`ui/`](./ui/) | React 18 + Vite 5 + TypeScript SPA (16 pages) |
| [`policies/dashboard_auth.py`](./policies/dashboard_auth.py) | Bearer-token guard (`BASELITHBOT_DASHBOARD_TOKEN`) |
| [`policies/rate_limit.py`](./policies/rate_limit.py) | Token-bucket rate limiter (per client/route) |
| [`policies/dm_policy.py`](./policies/dm_policy.py) | DM pairing policy for inbound events |
| [`policies/host_acl.py`](./policies/host_acl.py) | Host allowlist |
| [`model_config.py`](./model_config.py) | `ModelPreferences` + persisted `ModelPreferenceStore` |
| [`usage.py`](./usage.py) | `UsageLedger` — LLM spend accumulator |
| [`run_tracker.py`](./run_tracker.py) | Live run state (bounded history) |
| [`metrics.py`](./metrics.py) | Prometheus counters/histograms |
| [`tracing.py`](./tracing.py) | OpenTelemetry shim |

---

## 4. Installation

### 4.1 Core dependencies (always required)

```bash
pip install playwright>=1.45.0 playwright-stealth>=1.0.6 httpx>=0.27.0 psutil>=5.9.0
playwright install chromium
```

### 4.2 Computer Use dependencies (only if you opt in)

```bash
pip install "pyautogui>=0.9.54" "mss>=9.0.1" "Pillow>=10.0.0"
```

> macOS: grant **Accessibility** + **Screen Recording** permission to your
> Python interpreter in *System Settings → Privacy & Security*.
> Linux headless: run inside `Xvfb :99 -screen 0 1280x720x24` or a VNC VM.

### 4.3 Build the React dashboard

```bash
cd plugins/baselithbot/ui
npm install
npm run build          # emits plugins/baselithbot/ui/dist
```

`ui/dist/**/*` is declared in [`pyproject.toml`](../../pyproject.toml)
under `[tool.setuptools.package-data]`, so `pip install baselith-core`
ships the built bundle automatically. The React dev server is available
at [http://localhost:5180](http://localhost:5180) with `npm run dev` — it
proxies `/baselithbot/*` to `http://localhost:8000`.

### 4.4 Enable the plugin

Edit [`configs/plugins.yaml`](../../configs/plugins.yaml):

```yaml
baselithbot:
  enabled: true
  headless: true
  max_steps: 20
```

Start the backend:

```bash
python backend.py              # or: baselith serve
baselith doctor                # environment probe
```

Navigate to [http://localhost:8000/baselithbot/](http://localhost:8000/baselithbot/).

---

## 5. Configuration reference

The plugin reads its block from `configs/plugins.yaml`. Each group maps
to a Pydantic model, so unknown keys raise at startup.

### 5.1 Top-level keys

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `enabled` | bool | `false` | Master toggle consumed by the plugin registry |
| `headless` | bool | `true` | Chromium `--headless=new` vs windowed |
| `max_steps` | int | `20` | Upper bound on the Observe→Plan→Act loop |
| `viewport_width` | int | `1280` | Playwright viewport |
| `viewport_height` | int | `720` | Playwright viewport |
| `stealth` | object | see §5.2 | Stealth countermeasures |
| `computer_use` | object | see §5.3 | OS-level controls (opt-in) |

### 5.2 `stealth:` — `StealthConfig`

| Key | Default | Notes |
|-----|---------|-------|
| `enabled` | `true` | Master toggle |
| `rotate_user_agent` | `true` | Pick a random UA from `user_agents` at start |
| `mask_webdriver` | `true` | `navigator.webdriver = undefined` |
| `spoof_languages` | `["en-US", "en"]` | `navigator.languages` + Accept-Language |
| `spoof_timezone` | `"UTC"` | `Intl.DateTimeFormat().resolvedOptions().timeZone` |
| `user_agents` | 3 built-in Chrome variants | Override to widen the pool |

Perturbations also hit `navigator.plugins`, `navigator.hardwareConcurrency`,
WebGL `UNMASKED_VENDOR_WEBGL`, and 2D canvas ImageData noise; see
[`stealth.py`](./stealth.py).

### 5.3 `computer_use:` — `ComputerUseConfig`

| Key | Default | Meaning |
|-----|---------|---------|
| `enabled` | `false` | **Master switch** — until flipped, *no* Computer Use tool runs |
| `allow_mouse` | `true` | Enables `mouse_move`, `mouse_click`, `mouse_scroll` |
| `allow_keyboard` | `true` | Enables `kbd_type`, `kbd_press`, `kbd_hotkey` |
| `allow_screenshot` | `true` | Enables `desktop_screenshot`, `screen_size` |
| `allow_shell` | `false` | Enables `shell_run` (needs `allowed_shell_commands`) |
| `allow_filesystem` | `false` | Enables `fs_read`, `fs_write`, `fs_list` (needs `filesystem_root`) |
| `allowed_shell_commands` | `[]` | First-token allowlist. Exact-match OR space-delimited prefix |
| `shell_timeout_seconds` | `30.0` | Hard timeout (1–600) per shell invocation |
| `filesystem_root` | `None` | Absolute path under which all fs ops are confined |
| `filesystem_max_bytes` | `10_000_000` | Per-write byte cap |
| `audit_log_path` | `None` | JSON-Lines path; when unset only structured logs are emitted |

See §16 for the safety model.

### 5.4 Secrets (read from environment)

| Env var | Purpose |
|---------|---------|
| `BASELITHBOT_DASHBOARD_TOKEN` | Bearer token for every dashboard write endpoint. Unset = open dev mode (warning logged once). |
| `ELEVENLABS_API_KEY` | Optional; enables the ElevenLabs voice provider |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GOOGLE_API_KEY` | LLM + Vision providers (read from `core.config.services`) |
| `TAILSCALE_AUTHKEY` | Gateway provisioning (optional) |

Keys are **never** echoed back by `/dash/models`; they stay in environment
variables under `core.config.services`.

### 5.5 Model preferences (persisted JSON)

Persisted to
`plugins/baselithbot/.state/model_preferences.json` via
[`ModelPreferenceStore`](./model_config.py). Defaults: provider
`ollama/llama3.2`, vision `openai/gpt-4o`, temperature `0.7`. Known provider
catalog (`KNOWN_PROVIDERS`, `KNOWN_VISION_PROVIDERS`) bounds every write so
the endpoint cannot smuggle arbitrary strings downstream. A `failover_chain`
(ordered list of `{provider, model, cooldown_seconds}`) is supported.

---

## 6. Lifecycle & state machine

`BaselithbotAgent` extends `core.lifecycle.mixins.LifecycleMixin` and
traverses the framework state machine:

```text
UNINITIALIZED ──startup()──▶ STARTING ──ready──▶ READY
                                                   │
                                          execute()│
                                                   ▼
READY ◀─── loop (Observe→Plan→Act) ───────────────┘
  │
  shutdown()
  ▼
STOPPING ──▶ STOPPED
```

- `execute()` refuses work unless `state == READY` and returns a failing
  `BaselithbotResult` with the current state if called early.
- `_do_health_check()` exposes `backend_started` + `stealth_enabled`
  through the framework health aggregator.
- `BaselithbotPlugin.shutdown()` additionally stops the cron scheduler
  and every live channel adapter.

---

## 7. HTTP API reference

All routes are mounted under **`/baselithbot`**. Write endpoints require
the dashboard bearer token when `BASELITHBOT_DASHBOARD_TOKEN` is set; reads
stay open. Rate limits are per client IP via
[`policies/rate_limit.py`](./policies/rate_limit.py).

### 7.1 Core routes

| Method | Path | Auth | RL | Body / Response |
|--------|------|------|----|-----------------|
| GET | `/baselithbot/` | — | — | 307 → `/baselithbot/ui/` |
| POST | `/baselithbot/run` | token | 10/min | `RunRequest` → `BaselithbotResult` |
| GET | `/baselithbot/status` | — | — | `StatusResponse` |
| POST | `/baselithbot/inbound/{channel}` | — | body≤1 MiB | `{status, channel, results}` |
| WS | `/baselithbot/ws/pair` | token in handshake | 20/min | `{token, node_id, platform}` → `{status:"paired", node}` |
| GET | `/baselithbot/metrics` | — | — | Prometheus text exposition |

`RunRequest` — [`router.py`](./router.py):

```json
{
  "run_id": "optional-string",
  "goal": "open hacker news and list top 3 stories",
  "start_url": "https://news.ycombinator.com",
  "max_steps": 20,
  "extract_fields": ["title", "url"]
}
```

`BaselithbotResult`:

```json
{
  "run_id": "run-1f6a…",
  "success": true,
  "final_url": "https://…",
  "steps_taken": 7,
  "extracted_data": {"title": "[extracted from …]"},
  "history": ["navigate: …", "click: …", "done: …"],
  "error": null,
  "last_screenshot_b64": "iVBOR…"
}
```

### 7.2 Dashboard routes (`/baselithbot/dash/*`)

All in [`ui_api.py`](./ui_api.py). Legend: 🔓 read-only, 🔒 bearer-token required.

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/dash/overview` | 🔓 | Aggregate snapshot: agent state, counts, inbound stats, usage, cron backend |
| GET | `/dash/sessions` | 🔓 | List sessions |
| POST | `/dash/sessions` | 🔒 30/min | Create session |
| GET | `/dash/sessions/{sid}/history` | 🔓 | Session messages |
| POST | `/dash/sessions/{sid}/send` | 🔒 30/min | Send a message |
| POST | `/dash/sessions/{sid}/reset` | 🔒 | Clear history |
| DELETE | `/dash/sessions/{sid}` | 🔒 20/min | Delete session |
| GET | `/dash/channels` | 🔓 | Known/live channels + inbound counters |
| GET | `/dash/skills?scope=…` | 🔓 | Skill catalog filtered by scope |
| GET | `/dash/crons` | 🔓 | Cron backend + scheduled jobs |
| POST | `/dash/crons/{name}/remove` | 🔒 20/min | Remove a cron job |
| GET | `/dash/nodes` | 🔓 | Paired nodes + pairing status |
| POST | `/dash/nodes/token` | 🔒 5/min | Issue pairing token |
| DELETE | `/dash/nodes/{node_id}` | 🔒 20/min | Revoke paired node |
| GET | `/dash/doctor` | 🔓 | Environment/dependency probe |
| GET | `/dash/canvas` | 🔓 | `CanvasSurface.snapshot()` |
| GET | `/dash/usage/summary` | 🔓 | Cost totals + by-model breakdown |
| GET | `/dash/usage/recent?limit=N` | 🔓 | Last N usage events |
| GET | `/dash/run-task/latest` | 🔓 | Latest run state |
| GET | `/dash/run-task/recent?limit=N` | 🔓 | Recent run states |
| GET | `/dash/run-task/{run_id}` | 🔓 | Specific run state |
| GET | `/dash/agents` | 🔓 | Sub-agent registry |
| GET | `/dash/workspaces` | 🔓 | Workspace runtime summaries |
| GET | `/dash/models` | 🔓 | Current prefs + catalog |
| PUT | `/dash/models` | 🔒 5/min | Update `ModelPreferences` (validated against catalog) |
| GET | `/dash/metrics/prometheus` | 🔓 | Prometheus text (JSON-wrapped passthrough) |
| GET | `/dash/events/recent?limit=N` | 🔓 | Replay last N dashboard events |
| GET | `/dash/events/stream` | 🔓 | Server-Sent Events stream (live) |

`DashboardEventBus` emits: `run.started`, `run.step`, `run.completed`,
`run.failed`, `session.created`, `session.message`, `session.reset`,
`session.deleted`, `cron.removed`, `node.token_issued`, `node.revoked`,
`models.updated`.

### 7.3 Static UI

`GET /baselithbot/ui/{path:path}` serves `ui/dist/`. Unknown paths fall
back to `index.html` so React Router client-side routes work. When the
bundle is missing, a graceful 503 with build instructions is returned.
All responses carry hardened headers:

```http
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: no-referrer
Permissions-Policy: microphone=(), camera=(), geolocation=()
```

---

## 8. Dashboard (React SPA)

Single-page application under [`ui/`](./ui/).

- **Stack** — React 18, Vite 5, TypeScript, vanilla CSS (design tokens,
  no Tailwind), `react-chartjs-2`, React Router, TanStack Query, SSE
  client.
- **Pages** — Overview, Run Task, Sessions, Channels, Skills, Crons,
  Nodes, Workspaces, Agents, Canvas, Doctor, Models, Metrics, Logs,
  NotFound.
- **Live events** — every page subscribes to `/dash/events/stream`
  through `DashboardProvider`, so charts update without polling.
- **Accessibility** — `useOverlayA11y` hook for focus trap / ESC handling,
  skip-to-content link, Lighthouse A11y audit gated in CI.
- **Security UX** — a token banner appears when the server warns
  `baselithbot_dashboard_open`; the banner offers a paste field that
  stores the token in `sessionStorage` only.

Build: `cd plugins/baselithbot/ui && npm run build`.
Dev proxy: `npm run dev` (Vite on `:5180`, proxies `/baselithbot/*`).

---

## 9. MCP tools (36)

Every tool is registered via `BaselithbotPlugin.get_mcp_tools()`. All tool
entry points return a `{"status": ..., ...}` dict and never raise — the
orchestrator owns error policy. Denials use
`{"status": "denied", "error": ...}`; runtime failures use
`{"status": "error", "error": ...}`.

### 9.1 Browser (7) — [`tools.py`](./tools.py)

| Tool | Purpose |
|------|---------|
| `baselithbot_navigate` | Navigate to URL (stealth applied) |
| `baselithbot_click` | CSS-selector click |
| `baselithbot_type` | Type text into an input |
| `baselithbot_scroll` | Scroll page by pixels |
| `baselithbot_screenshot` | Return base64 PNG |
| `baselithbot_eval_js_safe` | Run a **whitelisted** JS snippet (see `js_whitelist.ALLOWED_SNIPPETS`); user args sanitized via `core.services.sanitization.InputSanitizer` |
| `baselithbot_run_task` | Full autonomous `goal → result` (wraps `BaselithbotAgent.execute`) |

### 9.2 Computer Use (12) — [`computer_tools.py`](./computer_tools.py)

| Tool | Capability gate |
|------|-----------------|
| `baselithbot_desktop_screenshot` / `_screen_size` | `allow_screenshot` |
| `baselithbot_mouse_move` / `_click` / `_scroll` | `allow_mouse` |
| `baselithbot_kbd_type` / `_press` / `_hotkey` | `allow_keyboard` |
| `baselithbot_shell_run` | `allow_shell` + `allowed_shell_commands` |
| `baselithbot_fs_read` / `_write` / `_list` | `allow_filesystem` + `filesystem_root` |

### 9.3 OpenClaw parity (17) — [`openclaw_tools.py`](./openclaw_tools.py)

`channel_list`, `channel_send`, `session_create`, `session_list`,
`session_history`, `session_send`, `session_reset`, `chat_command`,
`doctor`, `skills_list`, `skills_inject`, `voice_tts`, `canvas_render`,
`cron_list`, `tailscale_status`, `node_pairing_token`, `paired_nodes`.

### 9.4 Extra tools — [`extra_tools.py`](./extra_tools.py)

Code editing (`code_diff_apply`, `code_line_edit`, `code_search_replace`
with `MultiFileEditor`), usage accounting (`usage_record`,
`usage_summary`), process control, Tailscale provisioning, workspace
lifecycle, and agent routing. All gated through `ComputerUseConfig`
where relevant.

---

## 10. Channels (24) & inbound dispatch

Registered via `channels/bootstrap.py → build_default_registry()`.

**First-party adapters (4):** Slack, Telegram, Discord, WebChat.
**Generic webhook adapters (20):** WhatsApp, Google Chat, Signal, iMessage,
BlueBubbles, IRC, Microsoft Teams, Matrix, Feishu, LINE, Mattermost,
Nextcloud Talk, Nostr, Synology Chat, Tlon, Twitch, Zalo, WeChat, QQ,
Generic.

### 10.1 Outbound

```python
await plugin.channels.send(
    ChannelMessage(channel="slack", target="#ops", text="…"),
    config={"webhook_url": "https://hooks.slack.com/…"},
)
```

or via MCP tool `baselithbot_channel_send`. Secrets in `config` (webhook
URLs, API keys) are redacted from structured logs.

### 10.2 Inbound

`POST /baselithbot/inbound/{channel}` with a raw provider payload (Slack
events API, Telegram update, Discord interaction, or anything else
flowing through `parse_generic`). The route:

1. Rejects bodies > 1 MiB.
2. Parses JSON; malformed payloads become `{"raw": "…"}`.
3. Normalizes into an `InboundEvent` via `inbound.parsers`.
4. Evaluates `dm_policy` — DM with an unpaired sender is rejected with
   `{"status": "denied", "reason": …}`.
5. Counts via Prometheus `INBOUND_EVENT_TOTAL{channel=…}`.
6. Dispatches into `InboundDispatcher.dispatch()` → registered handlers.

---

## 11. Sessions & sandbox

`SessionManager` keeps per-session `SessionMessage` history in memory;
each session can declare a `primary` flag. `DockerSandbox` wraps an
optional per-session Docker container for isolated tool execution — if
the Docker daemon is unavailable the manager falls back to an in-process
execution context and the doctor probe surfaces the degradation.

Dashboard workflow: create → send → watch live updates via SSE → reset
or delete. Slash commands `/new`, `/reset`, `/compact` operate on the
active session through `ChatCommandRouter` + `SlashRuntimeState`.

---

## 12. Skills registry (ClawHub)

`SkillRegistry` tracks skills across three scopes: `bundled` (shipped),
`managed` (installed via ClawHub), `workspace` (per-project). Each skill
can export `AGENTS.md` / `SOUL.md` / `TOOLS.md`; `skills_inject` returns
a concatenated injection bundle consumable by the orchestrator prompt
builder.

Loader: [`skills/loader.py`](./skills/loader.py) resolves skill paths,
reads markdown, and exposes them through the `/dash/skills` endpoint.

---

## 13. Cron scheduler

`CronScheduler` ([`cron.py`](./cron.py)) runs an asyncio task that polls
every second against registered jobs. Each job declares `name`, `crontab`
(standard 5-field), and a handler coroutine. Backend label (e.g.
`"asyncio"`) is reported by `/dash/overview` and `/dash/crons`.

- `cron_list` MCP tool → read-only listing.
- `POST /dash/crons/{name}/remove` (🔒) → delete one job, publishes
  `cron.removed` on the SSE bus.

---

## 14. Node pairing & gateway

### 14.1 Node pairing

- `POST /dash/nodes/token` (🔒, 5/min) issues a short-lived pairing
  token.
- `WS /baselithbot/ws/pair` accepts the handshake
  `{token, node_id, platform}` and replies `{status: "paired", node}`.
- Once paired, subsequent chat messages are echoed with `ack: …`
  (placeholder for command family routing — see `nodes/commands.py`).

### 14.2 Gateway

- `TailscaleGateway.status()` shells out to `tailscale status --json`.
- `gateway/ssh.py` runs remote commands via subprocess with an
  allowlisted argv (no shell).
- `gateway/tailscale_provisioning.py` handles oauth key provisioning
  when `TAILSCALE_AUTHKEY` is set.

---

## 15. Voice & Canvas (A2UI)

**Voice** (`voice/`):

- `SystemTTS` — `say` on macOS, `espeak` on Linux; works offline.
- `ElevenLabsTTS` — HTTP client; opt-in via `ELEVENLABS_API_KEY`.
- `WakeStateMachine` — idle → listening → thinking → speaking.

**Canvas / A2UI** (`canvas/`):

- `CanvasSurface` — append-only widget list (`Text`, `Button`, `Image`,
  `List`).
- `A2UIRenderer` — serializes the surface into Anthropic A2UI JSON.
- `GET /dash/canvas` returns a snapshot; the UI page `Canvas.tsx`
  renders it.

---

## 16. Computer Use safety model

Implements the Anthropic Computer Use safety recipe end-to-end.

1. **Master switch** — `computer_use.enabled = false` by default; tools
   immediately return `{status: "denied"}` without touching the OS.
2. **Capability flags** — `allow_mouse`, `allow_keyboard`,
   `allow_screenshot`, `allow_shell`, `allow_filesystem` gate
   independently. `require_enabled("shell")` raises `ComputerUseError`
   when both the master or the per-capability flag is off.
3. **Shell allowlist** — first token (split with `shlex`) of every
   invocation must match `allowed_shell_commands` by exact-match OR
   space-prefix (`"git status"` allows `git status --short` but not
   `git push`). `shell=False` always — argv vector, never a string.
4. **Shell timeout** — hard kill at `shell_timeout_seconds` (default
   30s). stdout/stderr truncated to reasonable bytes.
5. **Filesystem scoping** — every path resolves via `Path.resolve()`
   and must `relative_to(filesystem_root)` — `..` traversal blocked.
   Per-write byte cap `filesystem_max_bytes`.
6. **Audit log** — JSON-Lines append to `audit_log_path` with batched
   flush. Sensitive keys (`token`, `password`, `secret`, `api_key`,
   `webhook_url`, …) are redacted via
   [`secret_redaction.py`](./secret_redaction.py) both from the log file
   and the structured log line.
7. **Denied vs error** — capability denials return `denied`; runtime
   failures return `error`. Neither raises to the orchestrator.

### 16.1 Recommended deployment posture

- Run under a dedicated unix user with a scoped `$HOME`.
- Set `filesystem_root` to a disposable directory (e.g.
  `/var/lib/baselithbot/workspace`).
- Keep `allowed_shell_commands` to the minimal surface needed.
- Pipe `audit_log_path` to a WORM/append-only volume.
- On Linux, run under `Xvfb` in a VM/container — do **not** grant mouse
  control to the workstation that holds your keys.

---

## 17. Model preferences (LLM/Vision failover)

`ModelPreferences` ([`model_config.py`](./model_config.py)) are the
operator-chosen `(provider, model)` pair plus optional failover chain.
Persisted atomically (`.tmp` + `os.replace`) to
`plugins/baselithbot/.state/model_preferences.json`.

LLM providers: `openai`, `anthropic`, `ollama`, `huggingface`.
Vision providers: `openai`, `anthropic`, `google`, `ollama`.

Each known model is catalog-bounded — the dashboard PUT request rejects
unknown names, so the endpoint cannot be abused to inject arbitrary
strings into downstream LLM clients. API keys stay in env vars.

Changes apply on the **next agent startup** — running tasks keep the
model they started with to avoid mid-task churn.

### Failover chain

```json
{
  "provider": "anthropic",
  "model": "claude-sonnet-4-6",
  "failover_chain": [
    {"provider": "openai", "model": "gpt-4o", "cooldown_seconds": 30.0},
    {"provider": "ollama", "model": "llama3.2", "cooldown_seconds": 5.0}
  ]
}
```

---

## 18. Authentication, rate limits, security headers

### 18.1 Bearer token

Every dashboard *write* endpoint and `POST /baselithbot/run` runs through
[`DashboardAuth`](./policies/dashboard_auth.py).

- **Configure** — `export BASELITHBOT_DASHBOARD_TOKEN=$(openssl rand -hex 32)`.
- **Presentation** — `Authorization: Bearer <token>` OR `?token=<token>`.
- **Comparison** — `hmac.compare_digest` (timing-safe).
- **Dev mode** — unset token → reads pass, writes pass **with a warning
  logged once** (`baselithbot_dashboard_open`). Not safe for multi-tenant
  deploys.

### 18.2 Rate limits

Per-client token-bucket limiters
([`policies/rate_limit.py`](./policies/rate_limit.py)):

| Route | Window | Max |
|-------|--------|-----|
| `POST /baselithbot/run` | 60s | 10 |
| `WS /baselithbot/ws/pair` | 60s | 20 |
| `POST /dash/sessions` | 60s | 30 |
| `POST /dash/sessions/{sid}/send` | 60s | 30 |
| `DELETE /dash/sessions/{sid}` | 60s | 20 |
| `POST /dash/crons/{name}/remove` | 60s | 20 |
| `POST /dash/nodes/token` | 60s | 5 |
| `DELETE /dash/nodes/{node_id}` | 60s | 20 |
| `PUT /dash/models` | 60s | 5 |

### 18.3 Inbound hardening

- Body cap 1 MiB.
- `DMPairingPolicy.evaluate()` gates DMs from unpaired senders.
- Redacted structured log line on every accepted event.

### 18.4 Static UI hardening

Served with `X-Content-Type-Options`, `X-Frame-Options: DENY`,
`Referrer-Policy: no-referrer`, restrictive `Permissions-Policy`.

---

## 19. Observability (metrics, audit, events)

### 19.1 Prometheus

`GET /baselithbot/metrics` renders the registry. Notable series
([`metrics.py`](./metrics.py)):

- `baselithbot_inbound_event_total{channel}`
- `baselithbot_run_total{result}`
- `baselithbot_run_steps` (histogram)
- `baselithbot_tool_errors_total{tool}`
- plus standard FastAPI/uvicorn process metrics.

A JSON passthrough is available at `/dash/metrics/prometheus` for dashboard
rendering.

### 19.2 Audit

`AuditLogger` writes JSON-Lines; each line contains `ts`, `action`, and
redacted fields. Recommended retention: ship to Loki or CloudWatch Logs
via Promtail/Fluent Bit with immutable retention.

### 19.3 Structured logs

All logs go through `core.observability.logging.get_logger`; every event
uses `snake_case` keys (`baselithbot_step`, `baselithbot_tool_error`, …)
so they are grep-friendly across plugins.

### 19.4 Traces

OpenTelemetry spans when `core.observability.tracing` is wired;
`tracing.py` is a thin shim that no-ops when OTEL is disabled.

### 19.5 Dashboard event bus

Process-wide `DashboardEventBus` (bounded 200-event ring buffer, 256-deep
per-subscriber queue). Backpressure is non-fatal — slow consumers are
dropped rather than blocking producers.

---

## 20. CLI reference

Registered via [`cli.py`](./cli.py) into `core.cli.__main__`.

```bash
# Execute one autonomous task
baselith baselithbot run "open hacker news and list top 3 stories" \
  --start-url https://news.ycombinator.com --max-steps 25

# Windowed browser (debug)
baselith baselithbot run "click the login button" --headed

# Print plugin manifest / version / readiness
baselith baselithbot status

# Interactive onboarding wizard
baselith baselithbot onboard                         # prints YAML block
baselith baselithbot onboard --write                 # writes to configs/plugins.yaml
baselith baselithbot onboard --write --config-path path/to/plugins.yaml
baselith baselithbot onboard --install-daemon        # install launchd/systemd unit
baselith baselithbot onboard --install-daemon --dry-run

# DM policy allowlist (persisted into configs/plugins.yaml)
baselith baselithbot pairing approve slack U12345ABC
baselith baselithbot pairing list
baselith baselithbot pairing token                   # one-shot dev pairing token

# Launch the FastAPI gateway
baselith baselithbot gateway --host 0.0.0.0 --port 18789 [--verbose]
baselith baselithbot gateway --install-daemon        # alias for onboard --install-daemon
```

All commands return JSON on stdout and a non-zero exit when the task
fails.

---

## 21. Programmatic usage (Python)

### 21.1 Direct agent

```python
import asyncio
from plugins.baselithbot import BaselithbotAgent, BaselithbotTask

async def main() -> None:
    agent = BaselithbotAgent(config={"headless": True, "max_steps": 25})
    await agent.startup()
    try:
        result = await agent.execute(
            BaselithbotTask(
                goal="search 'baselithcore' on duckduckgo and return top 3",
                start_url="https://duckduckgo.com",
                extract_fields=["title", "url"],
            ),
            context={"run_id": "demo-1", "on_progress": lambda p: print(p)},
        )
        print(result.model_dump_json(indent=2))
    finally:
        await agent.shutdown()

asyncio.run(main())
```

### 21.2 From a plugin

```python
from plugins.baselithbot import BaselithbotPlugin

plugin = BaselithbotPlugin()
await plugin.initialize({"headless": True})
agent = await plugin.get_or_start_agent()
# use agent.execute(...)
await plugin.shutdown()
```

### 21.3 Orchestrator intent

`BaselithbotFlowHandler.handle_browse(query, context)` is invoked by the
BaselithCore orchestrator whenever the user utterance matches the
`baselithbot_browse` intent patterns
(`"baselithbot"`, `"browse autonomously"`, `"navigate web"`,
`"automate browser"`, `"scrape stealth"`, `"stealth browse"`).

---

## 22. Testing

```bash
# Plugin-wide
python -m pytest tests/unit/plugins_tests/test_baselithbot_plugin.py -v

# Scoped
python -m pytest tests/unit/plugins_tests/ -k baselithbot
python -m pytest tests/unit/plugins_tests/ -k baselithbot -m "not slow"

# With coverage (project gate is 54%)
python -m pytest --cov=plugins/baselithbot --cov-report=html
```

Test doubles:

- `BrowserAgent` replaced with a fake that returns scripted
  `BrowserAction`s.
- `pyautogui`, `mss`, `psutil` are monkeypatched.
- Vision + LLM services are mocked at the `core.services` boundary.
- Subprocess tests use `capfd` to assert the argv vector (never string).

---

## 23. Deployment recipes

### 23.1 Docker (headless)

```dockerfile
FROM mcr.microsoft.com/playwright/python:v1.45.0-jammy
WORKDIR /app
COPY . .
RUN pip install -e ".[dev]" \
 && playwright install chromium
ENV BASELITHBOT_DASHBOARD_TOKEN=set-at-runtime
EXPOSE 8000
CMD ["python", "backend.py"]
```

### 23.2 Behind nginx

```nginx
location /baselithbot/ {
  proxy_pass         http://127.0.0.1:8000;
  proxy_http_version 1.1;
  proxy_set_header   Host $host;
  proxy_set_header   Upgrade $http_upgrade;
  proxy_set_header   Connection "upgrade";   # WS + SSE
  proxy_buffering    off;                    # SSE
  proxy_read_timeout 3600s;
}
```

### 23.3 Systemd (VM target for Computer Use)

```ini
[Service]
User=baselithbot
Environment="DISPLAY=:99"
Environment="BASELITHBOT_DASHBOARD_TOKEN=%I"
ExecStartPre=/usr/bin/Xvfb :99 -screen 0 1280x720x24
ExecStart=/usr/bin/python backend.py
Restart=on-failure
```

---

## 24. Troubleshooting & FAQ

**Dashboard returns 401** — set `BASELITHBOT_DASHBOARD_TOKEN` and present
it as `Authorization: Bearer <token>` or `?token=<token>`.

**`baselithbot_dashboard_open` warning in logs** — dev mode is active
because no token is configured. Set one before exposing the server.

**`Computer Use is disabled`** — flip
`baselithbot.computer_use.enabled = true` in `configs/plugins.yaml`
*and* flip the specific `allow_*` capability you need.

**`capability 'shell' is not allowed`** — set `allow_shell: true` **and**
populate `allowed_shell_commands`. Remember the allowlist is first-token
or space-prefix match, not substring.

**`filesystem path escapes root`** — the target resolved to a path
outside `filesystem_root`. Fix the path; the plugin refuses to follow
`..` or symlinks that cross the root.

**`rate limit exceeded`** — client IP exhausted the bucket for that
route. Tune the limiter or back off.

**Chromium fails to launch** — run
`playwright install chromium --with-deps` on Linux; on macOS confirm the
bundle has Accessibility + Screen Recording permission.

**Inbound 413** — the body exceeded 1 MiB; chunk upstream or trim
payload.

**`pyautogui.FailSafeException`** — mouse hit a screen corner (built-in
safety). Disable with `pyautogui.FAILSAFE = False` only on throwaway
VMs.

**React bundle 503** — `ui/dist` missing. Build it
(`cd plugins/baselithbot/ui && npm install && npm run build`) or ship
the package so `ui/dist/**/*` is included.

**Model update rejected** — the posted `{provider, model}` is not in
`KNOWN_PROVIDERS` / `KNOWN_VISION_PROVIDERS`. Add the entry to the
catalog if you vetted it.

---

## 25. Roadmap

- **V1.1** — diff-screenshot vision feedback loop (detect UI changes
  between steps to cut redundant re-planning).
- **V1.2** — multi-session manager: parallel BrowserContexts under one
  agent for concurrent runs.
- **V1.3** — DOM-LLM semantic selector synthesis (no more hand-written
  CSS selectors in MCP tool args).
- **V1.4** — per-session Docker sandboxing for Computer Use shell.
- **V2.x** — full dashboard RBAC (roles per endpoint, OIDC provider),
  cross-plugin Canvas A2UI handoff, remote Tailscale-only control plane.

---

## 26. Marketplace publication

Baselithbot is self-contained under [`plugins/baselithbot/`](./) — the
directory can be extracted into a standalone git repository and
published to the [Baselith Marketplace](https://marketplace.baselithcore.xyz/)
hub. Full workflow, validator-compliance checklist, release cadence,
and prod ↔ standalone sync strategies live in
[`docs/publishing.md`](./docs/publishing.md).

Minimum additions before the first submission:

1. Add `LICENSE` (AGPL-3.0 — matches the core copyleft obligation).
2. Add `requirements.txt` mirroring `python_dependencies` plus
   `baselith-core>=0.6.0,<1.0.0`.
3. Patch `manifest.yaml` with `id: baselithbot`,
   `entry_point: plugin:BaselithbotPlugin`, and a `repository:` URL.
4. Run `baselith marketplace validate <path>` — must return zero errors.
5. `baselith marketplace login` → `baselith marketplace publish <path>`.

Submissions land in `PENDING`, pass an automated Bandit scan, then
enter admin review before appearing in the public registry.

---

## References

- `README.md` — quick start & feature matrix.
- `manifest.yaml` — plugin metadata & dependencies.
- `../../CLAUDE.md` — repository-level architectural guidance.
- [`scripts/check_architecture_boundaries.py`](../../scripts/check_architecture_boundaries.py) — Sacred Core enforcement.
- [`scripts/check_official_plugin_typing.py`](../../scripts/check_official_plugin_typing.py) — strict mypy gate for official plugins (applies here).
