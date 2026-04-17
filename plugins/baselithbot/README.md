# baselithbot

Autonomous web navigation agent for BaselithCore. OpenClaw-style cognitive loop
(Observe → Plan → Act) layered on top of the official `browser_agent` plugin's
Playwright backend.

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

## Components

| File | Role |
|------|------|
| `plugin.py` | `BaselithbotPlugin` registration entry point. |
| `agent.py` | `BaselithbotAgent` cognitive loop. |
| `tools.py` | 7 MCP tools (`baselithbot_navigate`, `_click`, `_type`, `_scroll`, `_screenshot`, `_eval_js_safe`, `_run_task`). |
| `handlers.py` | `BaselithbotFlowHandler.handle_browse` for orchestrator dispatch. |
| `router.py` | FastAPI router exposing `POST /api/baselithbot/run`, `GET /api/baselithbot/status`. |
| `stealth.py` | `apply_stealth(context)` and `pick_user_agent`. |
| `js_whitelist.py` | `ALLOWED_SNIPPETS` dict for safe in-page JS. |
| `types.py` | Pydantic models (`BaselithbotTask`, `BaselithbotResult`, `StealthConfig`). |
| `cli.py` | `baselith baselithbot run "<goal>"` CLI extension. |

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
```

## Install

```bash
pip install playwright>=1.45.0 playwright-stealth>=1.0.6
playwright install chromium
```

## Quick start

### Via REST

```bash
curl -X POST http://localhost:8000/api/baselithbot/run \
  -H "Content-Type: application/json" \
  -d '{"goal": "search anthropic on duckduckgo and report top result", "start_url": "https://duckduckgo.com"}'
```

### Via CLI

```bash
baselith baselithbot run "open hacker news and list top 3 stories"
```

### Programmatically

```python
from plugins.baselithbot import BaselithbotAgent, BaselithbotTask

agent = BaselithbotAgent(config={"headless": True})
await agent.startup()
result = await agent.execute(BaselithbotTask(goal="search 'baselithcore'"))
await agent.shutdown()
```

## Architecture invariants respected

- Lives entirely under `plugins/` (Sacred Core rule).
- No `core → plugins` imports.
- All files ≤500 LOC.
- Composes `plugins.browser_agent.agent.BrowserAgent` instead of duplicating
  Playwright wiring.

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
- **Audit log**: every privileged action is appended (JSON Lines) to `audit_log_path` and emitted as a structured log line.
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

## Roadmap

- V1.1: diff-screenshot vision feedback loop.
- V1.2: multi-session manager (parallel BrowserContexts).
- V1.3: DOM-LLM semantic selector synthesis.
- V1.4: Docker per-session sandboxing for Computer Use shell.
