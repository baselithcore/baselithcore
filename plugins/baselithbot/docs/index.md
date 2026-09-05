# BaselithBot — Autonomous Multi-Channel Agent

BaselithBot is BaselithCore's flagship autonomous agent plugin. It bundles an
OpenClaw-parity capability set — a goal-driven browser agent, OS-level
Computer Use, human-in-the-loop approvals, time-travel replay, a 24-adapter
messaging layer, a Live Canvas (A2UI) surface, voice, cron, node pairing, a
skills registry, and an extensive MCP tool surface — into a single plugin
mounted at `/baselithbot`, operated through a 20-page React dashboard.

!!! tip "In one sentence"
    Give BaselithBot a goal; it drives a real (stealth-hardened) browser or
    the host OS itself through an Observe → Plan → Act loop, pausing for
    operator approval on privileged actions, recording every step for
    time-travel debugging, and exposing the same capabilities to any MCP
    client or messaging channel.

## The stack

```
Goal (dashboard / CLI / MCP / chat channel / orchestrator intent)
        │
        ▼
BaselithbotAgent — Observe → Plan → Act loop over BrowserAgent
        │                                   │
        │ per-step callback                 ▼
        ▼                            Stealth countermeasures + Set-of-Mark
RunTracker + TaskReplayStore (SQLite)        overlay for VLM-accurate clicks
        │
        ▼
Dashboard SSE event bus ──► React control plane (20 pages)

Computer Use (opt-in, off by default)
  mouse / keyboard / screenshot / shell / filesystem
        │
        ▼
ApprovalGate (human-in-the-loop) ──► AuditLogger (JSON-Lines, redacted)

Channels (24 adapters) ──► InboundDispatcher ──► Sessions / Cron / Skills / Canvas / Voice
```

## What each surface does

| Surface | What it's for |
|---|---|
| **Overview** | Operational snapshot: agent state, live/registered channels, sessions, usage tokens/cost/latency, provider-key coverage, a live SSE event feed, inbound event counts, subsystem roster. |
| **Run Task** | Dispatch a browser run (goal, start URL, max steps, extraction fields), watch it live step-by-step, and browse recent/linked runs. |
| **Sessions** | Bounded per-conversation message histories — create, message (plain text launches a linked browser task; `/slash` commands execute inline), reset, delete. |
| **Channels** | Operational view of the 24 messaging adapters — readiness, inbound traffic, credential health; configure, start/stop/test each one. |
| **Skills** | The OpenClaw-compatible skills registry (bundled / managed / workspace scopes), the ClawHub remote catalog, and an in-browser skill author. |
| **Cron** | The async cron scheduler — built-in jobs plus operator-defined custom jobs (interval, enable/disable, run-now). |
| **Nodes** | Remote node pairing over WebSocket — issue one-shot tokens, review paired nodes, revoke access. |
| **Models** | LLM + Vision provider/model preferences and the encrypted per-provider API key vault. |
| **Agents** | The sub-agent registry — built-in and operator-defined custom agents, with a dispatch console. |
| **Workspaces** | Multi-workspace management with per-workspace channel overrides and skill discovery roots. |
| **Metrics** | LLM usage/cost trends by model, plus the raw Prometheus exposition. |
| **Canvas** | The Live Canvas (A2UI) surface — render/clear/dispatch widget sets that render as Anthropic A2UI JSON for any client. |
| **Computer Use** | The OS-level Computer Use *policy* editor — capability flags, shell allowlist, filesystem root, human-in-the-loop approval requirements. |
| **Desktop Task** | The interactive Computer Use *runner* — mouse/keyboard/screenshot/shell/filesystem tool catalog, goal-driven desktop runs, live inspector. |
| **Stealth** | Browser stealth countermeasures — UA rotation, `navigator.webdriver` masking, language/timezone spoofing, user-agent pool. |
| **Audit Log** | Tail-readable JSON-Lines audit trail of every privileged action, with secret redaction and status badges. |
| **Approvals** | The human-in-the-loop queue — pending Computer Use requests with a countdown, approve/deny with a reason, resolution history. |
| **Replay** | Time-travel debugger — scrub step-by-step through any recorded run's screenshots, reasoning, and extracted data. |
| **Live Logs** | Real-time Server-Sent Events from the dashboard event bus, filterable by type or payload substring. |
| **Doctor** | Environment/dependency/live-plugin-state probe — Python version, Playwright/Chromium, `pyautogui`/`mss`, Docker, Tailscale, macOS permission hints. |

## Who it's for

- **Automation engineers** who need a goal-driven browser agent with real
  stealth countermeasures instead of a brittle script.
- **Operators of desktop-control workflows** who need OS-level actions
  gated behind an explicit, auditable human-in-the-loop approval queue.
- **Teams wiring multi-channel agent access** — Slack, Telegram, Discord,
  WhatsApp and 20 more — into one operator console.
- **MCP / orchestrator integrators** who want the same 37+ tools available
  to any Model Context Protocol client, not just the bundled dashboard.

## Design principles

- **Off by default, gated when on.** OS-level Computer Use ships disabled
  (`enabled: false`); every capability flag, the shell allowlist, and the
  filesystem root are independent, explicit opt-ins — see
  [Security](reference/security.md).
- **Human-in-the-loop for privileged actions.** Any capability listed in
  `require_approval_for` parks in the [Approvals](guide/approvals.md) queue
  until an operator decides, with a timeout that auto-denies.
- **Every step is replayable.** Screenshots, reasoning, and extracted data
  for every run are persisted to SQLite for time-travel debugging — see
  [Replay](guide/replay.md).
- **Framework-native where it counts.** BaselithBot reuses BaselithCore's
  plugin lifecycle, MCP server, structured observability, and (optionally)
  the framework auth seam rather than re-implementing them — see
  [Architecture](reference/architecture.md).

## Next steps

- New here? Start with **[Getting started](getting-started.md)** — install,
  build the dashboard, and run your first task.
- Enabling Computer Use, channels, cron, or voice? See the
  **[Operations runbook](operations/runbook.md)**.
- Integrating or operating BaselithBot? See **[Architecture](reference/architecture.md)**,
  **[Configuration](reference/configuration.md)** and **[Security & RBAC](reference/security.md)**.
