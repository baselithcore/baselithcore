# MCP tools (37+)

[← Index](./README.md)

Every tool registered via `BaselithbotPlugin.get_mcp_tools()`. All entry
points return `{"status": ..., ...}` dicts and never raise — orchestrator
owns error policy.

Status conventions:

- `{"status": "success", ...}` — OK
- `{"status": "denied", "error": "..."}` — capability gate refused
- `{"status": "error", "error": "..."}` — runtime failure

## 1. Browser (7) — [`tools.py`](../tools.py)

| Tool | Purpose |
|------|---------|
| `baselithbot_navigate` | Navigate to URL (stealth applied) |
| `baselithbot_click` | CSS-selector click |
| `baselithbot_type` | Type text into an input |
| `baselithbot_scroll` | Scroll page by pixels |
| `baselithbot_screenshot` | Return base64 PNG |
| `baselithbot_eval_js_safe` | Run **whitelisted** JS snippet (`js_whitelist.ALLOWED_SNIPPETS`); user args sanitized via `core.services.sanitization.InputSanitizer` |
| `baselithbot_run_task` | Full autonomous `goal → result` (wraps `BaselithbotAgent.execute`) |

## 2. Computer Use (12) — [`computer_tools.py`](../computer_tools.py)

| Tool | Capability gate |
|------|-----------------|
| `baselithbot_desktop_screenshot` / `_screen_size` | `allow_screenshot` |
| `baselithbot_mouse_move` / `_click` / `_scroll` | `allow_mouse` |
| `baselithbot_kbd_type` / `_press` / `_hotkey` | `allow_keyboard` |
| `baselithbot_shell_run` | `allow_shell` + `allowed_shell_commands` |
| `baselithbot_fs_read` / `_write` / `_list` | `allow_filesystem` + `filesystem_root` |

Safety details: [computer-use.md](./computer-use.md).

## 3. OpenClaw parity (17) — [`openclaw_tools.py`](../openclaw_tools.py)

| Tool | Purpose |
|------|---------|
| `baselithbot_channel_list` | Known channel adapters |
| `baselithbot_channel_send` | Send outbound message (`channel`, `target`, `text`, `config`, `metadata`) |
| `baselithbot_session_create` | New chat session (`title`, `primary`) |
| `baselithbot_session_list` | Enumerate sessions |
| `baselithbot_session_history` | Fetch messages |
| `baselithbot_session_send` | Append message to session |
| `baselithbot_session_reset` | Clear session history |
| `baselithbot_chat_command` | Dispatch a `/command args` line |
| `baselithbot_doctor` | Dependency/environment probe |
| `baselithbot_skills_list` | List skills (bundled / managed / workspace) |
| `baselithbot_skills_inject` | Return `AGENTS.md` + `SOUL.md` + `TOOLS.md` bundle |
| `baselithbot_voice_tts` | System TTS (macOS `say` / Linux `espeak`) |
| `baselithbot_canvas_render` | Render a Canvas widget set as A2UI JSON |
| `baselithbot_cron_list` | Inspect scheduled jobs |
| `baselithbot_tailscale_status` | `tailscale status --json` |
| `baselithbot_node_pairing_token` | Issue a short-lived pairing token |
| `baselithbot_paired_nodes` | List currently paired nodes |

## 4. Extras — [`extra_tools.py`](../extra_tools.py)

Code editing, usage accounting, process control, Tailscale provisioning,
workspace lifecycle, agent routing. All gated through `ComputerUseConfig`
where relevant.

### 4.1 Code editing

- `baselithbot_code_diff_apply` — Apply unified diff via `ScopedFileSystem`.
- `baselithbot_code_line_edit` — Structured line-range edits (`LineRangeEdit`).
- `baselithbot_code_search_replace` — Multi-pattern search/replace.
- `baselithbot_code_multi_file_edit` — Atomic multi-file edit batch.

### 4.2 Usage / workspace / agents

- `baselithbot_usage_record` / `_summary` — Ledger read/write.
- `baselithbot_workspace_create` / `_list` / `_activate` / `_destroy`.
- `baselithbot_agent_route` — Dispatch to sub-agent in `AgentRegistry`.

### 4.3 Process / gateway

- `baselithbot_process_list` / `_kill` — `psutil`-backed process control
  (`allow_shell` gate).
- `baselithbot_tailscale_up` / `_provision` — Auth-key provisioning via
  `TAILSCALE_AUTHKEY`.

## 4bis. Set-of-Mark — [`som.py`](../som.py)

- `baselithbot_som_annotate(max_marks=60, clear_after=False)` — Inject
  numbered overlay boxes on every clickable element in the active
  Playwright page and return the mark metadata (index, tag, role, text,
  href, bbox). The vision LLM can then reference elements by mark index
  instead of raw coordinates, which materially improves click accuracy.
  Details: [set-of-mark.md](./set-of-mark.md).

## 5. Error modes summary

| Mode | Trigger | Envelope |
|------|---------|----------|
| Denied | `ComputerUseError` | `{"status":"denied","error":"..."}` |
| Error | Any other `Exception` | `{"status":"error","error":"..."}` + `baselithbot_*_tool_error` log |
| Success | All checks passed | `{"status":"success", ...}` |

## 6. Tool authoring contract (internal)

Factory pattern (`build_*_tool_definitions`) returns `list[dict[str, Any]]`
entries shaped as:

```python
{
    "name": "baselithbot_xxx",
    "description": "...",
    "input_schema": {...},      # JSON Schema
    "handler": async_callable,  # returns status envelope
}
```

Shared state is captured via closure so tests can inject doubles. No tool
may raise — wrap every branch with the `_denied` / `_error` helpers.
