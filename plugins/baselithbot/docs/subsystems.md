# Subsystems

[← Index](./README.md)

Non-browser subsystems bundled in the plugin: channels, sessions, skills,
cron, nodes, gateway, voice, canvas, inbound.

## 1. Channels (24 adapters)

Registered via `channels/bootstrap.py → build_default_registry()`.

- **First-party (4)** — Slack, Telegram, Discord, WebChat.
- **Generic webhook (20)** — WhatsApp, Google Chat, Signal, iMessage,
  BlueBubbles, IRC, Microsoft Teams, Matrix, Feishu, LINE, Mattermost,
  Nextcloud Talk, Nostr, Synology Chat, Tlon, Twitch, Zalo, WeChat, QQ,
  Generic.

### 1.1 Outbound

```python
await plugin.channels.send(
    ChannelMessage(channel="slack", target="#ops", text="…"),
    config={"webhook_url": "https://hooks.slack.com/…"},
)
```

Or via MCP tool `baselithbot_channel_send`. Secrets in `config` (webhook
URLs, API keys) redacted from structured logs.

### 1.2 Inbound

`POST /baselithbot/inbound/{channel}` — pipeline detailed in
[http-api.md §4](./http-api.md#4-post-baselithbotinboundchannel).

Parsers in [`inbound/parsers.py`](../inbound/parsers.py) normalize
provider payloads into `InboundEvent`. `DMPairingPolicy` rejects DMs
from unpaired senders; `InboundDispatcher` fans events out to registered
handlers. Per-channel counters exposed on Prometheus.

## 2. Sessions & sandbox

[`sessions/manager.py`](../sessions/manager.py) keeps per-session
`SessionMessage` history in memory; each session can declare a `primary`
flag. [`sessions/sandbox.py`](../sessions/sandbox.py) wraps an optional
per-session Docker container for isolated tool execution.

Fallback behaviour: when the Docker daemon is unavailable, manager falls
back to in-process execution context and [`doctor.py`](../doctor.py)
surfaces the degradation.

Dashboard workflow: create → send → watch live updates via SSE → reset
or delete. Slash commands `/new`, `/reset`, `/compact` operate on the
active session through `ChatCommandRouter` + `SlashRuntimeState`.

## 3. Skills registry (ClawHub)

`SkillRegistry` tracks skills across three scopes:

- `bundled` — shipped with the plugin
- `managed` — installed via ClawHub
- `workspace` — per-project

Each skill can export `AGENTS.md` / `SOUL.md` / `TOOLS.md`.
`skills_inject` returns a concatenated injection bundle consumable by
the orchestrator prompt builder.

Loader: [`skills/loader.py`](../skills/loader.py) resolves paths, reads
markdown, exposes them through `/dash/skills`.

## 4. Cron scheduler

[`cron.py`](../cron.py) — `CronScheduler` runs an asyncio task that
polls every second against registered jobs. Each job declares `name`,
`crontab` (standard 5-field), handler coroutine. Backend label (e.g.
`"asyncio"`) reported via `/dash/overview` and `/dash/crons`.

Interactions:

- `baselithbot_cron_list` MCP tool — read-only listing.
- `POST /dash/crons/{name}/remove` (🔒) — delete, publishes
  `cron.removed` on SSE bus.

## 5. Node pairing

[`nodes/pairing.py`](../nodes/pairing.py) + [`nodes/commands.py`](../nodes/commands.py).

Lifecycle:

1. `POST /dash/nodes/token` (🔒, 5/min) — issues short-lived pairing
   token.
2. `WS /baselithbot/ws/pair` — accepts handshake
   `{token, node_id, platform}` → `{status: "paired", node}`.
3. Paired node can send text frames; server replies `ack: <first 200>`.
4. `DELETE /dash/nodes/{node_id}` — revoke.

Command families (Connect / Chat / Voice) defined in
[`nodes/commands.py`](../nodes/commands.py).

## 6. Gateway

[`gateway/`](../gateway/):

- `TailscaleGateway.status()` — shells out to `tailscale status --json`.
- `gateway/ssh.py` — remote command execution via subprocess with
  allowlisted argv (no shell).
- `gateway/tailscale_provisioning.py` — oauth key provisioning when
  `TAILSCALE_AUTHKEY` is set.

## 7. Voice / Audio

[`voice/`](../voice/):

- `SystemTTS` — `say` on macOS, `espeak` on Linux; works offline.
- `ElevenLabsTTS` — HTTP client; opt-in via `ELEVENLABS_API_KEY`.
- `WakeStateMachine` — `idle → listening → thinking → speaking`.

MCP tool: `baselithbot_voice_tts` — defaults to `SystemTTS`; if
ElevenLabs credentials present, UI selector switches provider.

## 8. Canvas / A2UI

[`canvas/`](../canvas/):

- `CanvasSurface` — append-only widget list (`Text`, `Button`, `Image`,
  `List`).
- `A2UIRenderer` — serializes surface into Anthropic A2UI JSON.
- `GET /dash/canvas` returns snapshot; UI page `Canvas.tsx` renders it.

MCP tool: `baselithbot_canvas_render` — append widgets and get the
serialized A2UI payload for client consumption.

## 9. Chat commands (slash surface)

[`chat_commands.py`](../chat_commands.py) — mirrors OpenClaw `/help`.

Supported commands:

`/status`, `/new`, `/reset`, `/compact`, `/think`, `/verbose`, `/trace`,
`/usage`, `/restart`, `/activation`.

Default handlers installed via
`install_default_handlers(chat_commands, sessions=…, usage=…)`
([`slash_defaults.py`](../slash_defaults.py)). `SlashRuntimeState` holds
per-command toggles (verbose mode, trace mode, etc.).

Unknown commands return `{"status": "ignored", "reason": "unknown command"}`
— router is side-effect-free for composition safety.

## 10. Usage ledger

[`usage.py`](../usage.py) — `UsageLedger` tracks LLM spend per event
(`provider`, `model`, `input_tokens`, `output_tokens`, `cost_usd`).
Exposed via:

- `baselithbot_usage_record` / `_summary` MCP tools.
- `GET /dash/usage/summary` — totals + by-model breakdown.
- `GET /dash/usage/recent?limit=N` — recent events.

[`usage_hooks.py`](../usage_hooks.py) provides framework-level hooks so
other plugins can push usage through the same ledger.

## 11. Run tracker

[`run_tracker.py`](../run_tracker.py) — bounded-history live run state.
Populated by `router.run()` on every step. Consumed by
`/dash/run-task/{latest,recent,<id>}` and the dashboard `RunTask.tsx`
page. Recent runs retained (default 32) with per-step screenshot
history.

## 12. Doctor

[`doctor.py`](../doctor.py) — async probe covering Python version,
Playwright install, `pyautogui` availability, Docker daemon, Tailscale
CLI, OS permissions. Exposed at `/dash/doctor` and as the
`baselithbot_doctor` MCP tool.
