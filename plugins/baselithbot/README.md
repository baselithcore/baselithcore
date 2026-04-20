# baselithbot

Full **OpenClaw-parity** local-first agent platform packaged as a single
BaselithCore plugin. Combines autonomous web navigation, OS-level Computer
Use, multi-channel inbox, Live Canvas A2UI, voice / TTS, sessions with Docker
sandboxing, skills registry, cron scheduling, node pairing, remote gateway
control, and more.

> Sacred Core compliant: lives entirely under `plugins/`, never touches
> `core/`, composes the existing `browser_agent` plugin.

## Features (V1.0.0)

- **Stealth mode**: navigator.webdriver masking, WebGL/Canvas fingerprint
  perturbation, Accept-Language spoofing, user-agent rotation.
- **Sanitized JS execution**: `eval_js_safe` MCP tool restricted to a
  whitelist of predefined snippets; user arguments go through
  `core.services.sanitization.InputSanitizer`.
- **Vision-driven planning**: each step takes a screenshot and asks
  `core.services.vision.VisionService` for the next `BrowserAction` as JSON.
- **Lifecycle compliant**: implements `LifecycleMixin`, transitions through
  `UNINITIALIZED → STARTING → READY → STOPPING → STOPPED`.
- **Runtime configuration overlay**: `computer_use` and `stealth` mutate
  live from the dashboard via `RuntimeConfigStore` (JSON, atomic, git-ignored);
  agent rebuilds automatically on save.
- **Human-in-the-loop approvals**: `ComputerUseConfig.require_approval_for`
  parks every privileged action in the `ApprovalGate` until a dashboard
  operator approves or denies (`approval_timeout_seconds` default 120s).
- **Time-travel replay**: every Observe → Plan → Act step persisted into
  SQLite (`replay.sqlite`) with screenshot + reasoning; dashboard shows
  scrubber UI. 14-day retention via cron.
- **Set-of-Mark vision**: `baselithbot_som_annotate` MCP tool injects
  numbered overlays on clickable elements so the VLM can reason by index
  instead of pixel coordinates.
- **Encrypted provider keys**: Fernet-encrypted `provider_keys.enc.json`
    - auto-generated `.secret_key`; dashboard never echoes plaintext (only
  `***<last4>` previews).
- **Backstage catalog integration**: [`catalog-info.yaml`](./catalog-info.yaml)
  wired into the portal, declares `component:default/browser_agent` as
  dependency.

## Components

### Browser layer (V1.0)

| File | Role |
|------|------|
| `plugin.py` | `BaselithbotPlugin` registration entry point. |
| `agent.py` | `BaselithbotAgent` cognitive Observe→Plan→Act loop. |
| `tools.py` | 7 browser MCP tools. |
| `handlers.py` | `BaselithbotFlowHandler.handle_browse`. |
| `router.py` | `POST /baselithbot/run`, `GET /status`. |
| `stealth.py` | Stealth countermeasures. |
| `js_whitelist.py` | `ALLOWED_SNIPPETS`. |
| `types.py` | Pydantic models. |
| `cli.py` | `baselith baselithbot {run,status}`. |

### Computer Use layer

| File | Role |
|------|------|
| `computer_use.py` | `ComputerUseConfig` + `AuditLogger` + `ComputerUseError`. |
| `os_control.py` | `OSController` (mouse/keyboard via pyautogui). |
| `desktop_vision.py` | `DesktopVision` (mss + Pillow). |
| `shell_exec.py` | `ShellExecutor` (allowlist + timeout, `shell=False`). |
| `filesystem.py` | `ScopedFileSystem` (root-scoped, anti-traversal). |
| `process_manager.py` | `ProcessManager` (psutil). |
| `computer_tools.py` | 12 Computer-Use MCP tools. |

### OpenClaw-parity layer

| Subsystem | Files | Notes |
|-----------|-------|-------|
| Multi-channel inbox | `channels/{base,registry,bootstrap,generic,webchat,slack,telegram,discord}.py` | 24 channels registered, 4 first-party + 20 generic-webhook. |
| Voice / Audio | `voice/{tts,elevenlabs,wake}.py` | System TTS (macOS `say` / Linux `espeak`), ElevenLabs HTTP, wake state machine. |
| Live Canvas + A2UI | `canvas/{surface,a2ui}.py` | `CanvasSurface`, widgets (`Text`/`Button`/`Image`/`List`), `A2UIRenderer`. |
| Sessions | `sessions/{manager,sandbox}.py` | `SessionManager` (list/history/send/reset), `DockerSandbox` per-session isolation. |
| Skills (ClawHub) | `skills/{registry,loader}.py` | Bundled / managed / workspace scopes; `AGENTS.md` / `SOUL.md` / `TOOLS.md` injection bundle. |
| Node pairing | `nodes/{pairing,commands}.py` | WebSocket pairing tokens, Connect/Chat/Voice command families. |
| Gateway | `gateway/{ssh,tailscale}.py` | Remote SSH command execution (allowlisted), Tailscale status query. |
| Integrations | `integrations/{webhooks,gmail_pubsub}.py` | Outbound webhook fan-out, Gmail Pub/Sub bridge. |
| Cron | `cron.py` | `CronScheduler` async loop. |
| Chat commands | `chat_commands.py` | `/status /new /reset /compact /think /verbose /trace /usage /restart /activation`. |
| Doctor | `doctor.py` | Environment + dependency probe. |
| OpenClaw MCP tools | `openclaw_tools.py` | 17 OpenClaw-parity MCP tools. |

### Control-plane + safety layer

| File | Role |
|------|------|
| `approvals.py` | `ApprovalGate` asyncio-native HITL pause/approve/deny/timeout. |
| `replay.py` | `TaskReplayStore` SQLite per-step recorder (screenshot + reasoning + URL). |
| `som.py` | Set-of-Mark DOM overlay + MCP tool wrapper. |
| `runtime_config.py` | JSON overlay for `computer_use` / `stealth` (dashboard-mutable). |
| `secret_store.py` | Fernet-encrypted provider keys at rest. |
| `_bootstrap.py` | Extracted init helpers to keep `plugin.py` < 500 LOC. |
| `dashboard/app.py` + `dashboard/routes/` | 15 route groups: diagnostics, agents, sessions, registry, channels, run_task, models, provider_keys, workspaces, canvas, computer_use, stealth, audit, approvals, replay, events. |

### MCP tool inventory (37+ total)

- 7 browser (`navigate`, `click`, `type`, `scroll`, `screenshot`, `eval_js_safe`, `run_task`)
- 12 Computer Use (`desktop_screenshot`, `screen_size`, `mouse_move`/`_click`/`_scroll`, `kbd_type`/`_press`/`_hotkey`, `shell_run`, `fs_read`/`_write`/`_list`)
- 17 OpenClaw parity (`channel_list`/`_send`, `session_create`/`_list`/`_history`/`_send`/`_reset`, `chat_command`, `doctor`, `skills_list`/`_inject`, `voice_tts`, `canvas_render`, `cron_list`, `tailscale_status`, `node_pairing_token`, `paired_nodes`)
- 1+ Set-of-Mark (`som_annotate`)
- extras: code-edit batch, process control, usage ledger, workspace lifecycle, agent routing — see [`docs/mcp-tools.md`](./docs/mcp-tools.md).

### Supported messaging channels (24)

WhatsApp, Telegram, Slack, Discord, Google Chat, Signal, iMessage, BlueBubbles,
IRC, Microsoft Teams, Matrix, Feishu, LINE, Mattermost, Nextcloud Talk, Nostr,
Synology Chat, Tlon, Twitch, Zalo, Zalo Personal, WeChat, QQ, WebChat.

## Configuration (`configs/plugins.yaml`)

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
    enabled: false          # opt-in
    allow_shell: false
    allow_filesystem: false
    allowed_shell_commands: ["ls", "pwd", "git status"]
    filesystem_root: "/var/lib/baselithbot/workspace"
    audit_log_path: "/var/log/baselithbot/computer_use.jsonl"
    require_approval_for: ["shell", "filesystem"]   # HITL gate
    approval_timeout_seconds: 120
```

All `computer_use` and `stealth` fields mutate live from the dashboard
(`PUT /dash/computer-use`, `PUT /dash/stealth`). Overrides persist to
`plugins/baselithbot/.state/runtime_config.json` and invalidate the
cached agent so the next run rebuilds with the new policy.

Env vars (full list in [`configs/.env.base`](../../configs/.env.base)):
`BASELITHBOT_DASHBOARD_TOKEN` (bearer on write endpoints),
`BASELITHBOT_SECRET_KEY` (Fernet master, auto-generated if unset).

## Install

```bash
pip install playwright>=1.45.0 playwright-stealth>=1.0.6
playwright install chromium
```

## Quick start

### Via REST

```bash
curl -X POST http://localhost:8000/baselithbot/run \
  -H "Content-Type: application/json" \
  -d '{"goal": "search anthropic on duckduckgo and report top result", "start_url": "https://duckduckgo.com"}'
```

### Via CLI

```bash
baselith baselithbot run "open hacker news and list top 3 stories"

# Onboarding wizard (prompts; writes configs/plugins.yaml block)
baselith baselithbot onboard --write

# Install native service unit (launchd on macOS, systemd user on Linux)
baselith baselithbot onboard --install-daemon

# DM policy allowlist
baselith baselithbot pairing approve slack U12345ABC
baselith baselithbot pairing list

# Launch the FastAPI gateway
baselith baselithbot gateway --host 0.0.0.0 --port 18789
```

### Programmatically

```python
from plugins.baselithbot import BaselithbotAgent, BaselithbotTask

agent = BaselithbotAgent(config={"headless": True})
await agent.startup()
result = await agent.execute(BaselithbotTask(goal="search 'baselithcore'"))
await agent.shutdown()
```

## Architecture invariants respected (BaselithCore)

- Lives entirely under `plugins/` (Sacred Core rule).
- No `core → plugins` imports (only `plugins → core` and `plugins → plugins`).
- All files ≤500 LOC.
- Composes `plugins.browser_agent.agent.BrowserAgent` instead of duplicating
  Playwright wiring.
- Pydantic-settings for every config object.
- Async/await for every I/O call.
- Google-style docstrings for every public class.
- Mocked LLM + Playwright + pyautogui in unit tests.
- Subprocess always invoked with `shell=False` (argv vector).
- Filesystem operations always re-resolved via `Path.resolve()` and asserted
  to remain inside the configured root.

## Dashboard (React + Vite)

The plugin ships a self-contained modern web dashboard to monitor and manage
every subsystem (agent state, sessions, channels, skills, cron jobs, paired
nodes, doctor report, usage/cost, live events stream).

**Stack:** React 18 + Vite 5 + TypeScript, vanilla CSS (design tokens, no
Tailwind), Chart.js via `react-chartjs-2`, React Router, TanStack Query,
Server-Sent Events for realtime.

### Endpoints

- **UI** — `GET /baselithbot/ui` serves the built SPA
  (`plugins/baselithbot/ui/dist/index.html`) with automatic fallback to
  `index.html` for client-side routes.
- **REST + SSE API** — `/baselithbot/dash/*`: `overview`, `sessions`,
  `channels`, `skills`, `crons`, `nodes`, `doctor`, `usage/{summary,recent}`,
  `metrics/prometheus`, `events/{recent,stream}`, `models`,
  `provider-keys`, `workspaces`, `canvas`, `run-task`, `agents`,
  `computer-use`, `stealth`, `audit-log`, `approvals`,
  `replay/runs` — see [`docs/dashboard.md`](./docs/dashboard.md).

### Pages (20)

Overview · RunTask · Sessions · Channels · Skills · Crons · Nodes ·
Workspaces · Agents · Canvas · Models · Metrics · Logs · Doctor ·
**ComputerUse** · **Stealth** · **AuditLog** · **Approvals** · **Replay** ·
NotFound.

### Build

```bash
cd plugins/baselithbot/ui
npm install
npm run build       # outputs plugins/baselithbot/ui/dist
```

Dev server with API proxy to the FastAPI backend on :8000:

```bash
npm run dev         # http://localhost:5180 (proxies /baselithbot/*)
```

The `ui/dist` directory is included in the Python package via
`[tool.setuptools.package-data]` (`ui/dist/**/*`), so the built bundle is
shipped as part of the plugin when installed.

## Tests

```bash
python -m pytest tests/unit/plugins_tests/test_baselithbot_plugin.py -v
```

## Computer Use layer (V1.0.0)

OS-level control implementing the Anthropic Computer Use safety pattern.
**Disabled by default — explicit opt-in required.**

### Capabilities

| Tool | Capability | Default |
|------|------------|---------|
| `baselithbot_desktop_screenshot` / `_screen_size` | `allow_screenshot` | on |
| `baselithbot_mouse_move` / `_click` / `_scroll` | `allow_mouse` | on |
| `baselithbot_kbd_type` / `_press` / `_hotkey` | `allow_keyboard` | on |
| `baselithbot_shell_run` | `allow_shell` + `allowed_shell_commands` | **off** |
| `baselithbot_fs_read` / `_write` / `_list` | `allow_filesystem` + `filesystem_root` | **off** |

### Safety model

- **Master switch**: `computer_use.enabled = false` by default; nothing runs.
- **Capability flags**: each subsystem has its own `allow_*` boolean.
- **Shell allowlist**: first token of every command must match `allowed_shell_commands` (exact or path-suffix). `shell=False` always; argv split via `shlex`. Hard timeout per `shell_timeout_seconds`.
- **Filesystem scoping**: every read / write / list resolves through `Path.resolve()` and must remain inside `filesystem_root` — `..` traversal blocked. Size cap `filesystem_max_bytes`.
- **Human-in-the-loop approvals**: capabilities listed in `require_approval_for` park every invocation in `ApprovalGate` until the dashboard operator approves or denies. Timeout → auto-deny + audit entry. See [`docs/approvals.md`](./docs/approvals.md).
- **Audit log**: every privileged action is appended (JSON Lines) to `audit_log_path` and emitted as a structured log line.
- **Time-travel replay**: every agent step persisted to SQLite (`replay.sqlite`); scrub the history from the `Replay` UI page. Retention 14 days via cron.
- **Denied vs error**: capability denials return `{"status": "denied", ...}` (never raise to the orchestrator); runtime failures return `{"status": "error", ...}`.

### Enable Computer Use

```yaml
baselithbot:
  computer_use:
    enabled: true
    allow_mouse: true
    allow_keyboard: true
    allow_screenshot: true
    allow_shell: true
    allow_filesystem: true
    allowed_shell_commands: ["echo", "ls", "cat", "git"]
    shell_timeout_seconds: 30
    filesystem_root: "/tmp/baselithbot-workspace"
    filesystem_max_bytes: 10000000
    audit_log_path: "/var/log/baselithbot/computer_use.jsonl"
```

### Install Computer Use dependencies

```bash
pip install pyautogui>=0.9.54 mss>=9.0.1 Pillow>=10.0.0
```

> **Note:** pyautogui requires accessibility permissions on macOS and a
> running display server on Linux. Recommended deployment target: a
> dedicated VM or container with a virtual framebuffer (Xvfb).

## Marketplace publication

Baselithbot can be extracted into a standalone git repository and
published to the [Baselith Marketplace](https://marketplace.baselithcore.xyz/)
hub. Full workflow (extraction, validator compliance, release cadence,
prod ↔ standalone sync) lives in
[`docs/publishing.md`](./docs/publishing.md).

Minimal first-submission delta:

1. `LICENSE` (AGPL-3.0 — matches the core copyleft obligation).
2. `requirements.txt` mirroring `python_dependencies` + `baselith-core>=0.6.0,<1.0.0`.
3. Patch `manifest.yaml`: `id`, `entry_point: plugin:BaselithbotPlugin`,
   `repository:` URL.
4. `baselith marketplace validate <path>` → `login` → `publish`.

## Roadmap

- V1.1: diff-screenshot vision feedback loop.
- V1.2: multi-session manager (parallel BrowserContexts).
- V1.3: DOM-LLM semantic selector synthesis.
- V1.4: Docker per-session sandboxing for Computer Use shell.
