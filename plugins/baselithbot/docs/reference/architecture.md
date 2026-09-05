# Architecture

## Design goals

BaselithBot targets six orthogonal capabilities behind a single plugin:

| Capability | Purpose |
|---|---|
| Autonomous browser agent | Goal-driven Observe → Plan → Act loop over Playwright |
| OS-level Computer Use | Mouse / keyboard / screenshot / shell / filesystem primitives |
| Human-in-the-loop gating | Per-capability approval requests bridged to the dashboard |
| Time-travel replay | SQLite-persisted per-step screenshots + reasoning |
| Messaging & orchestration | 24 channel adapters, sessions, cron, pairing, skills |
| Operator control plane | Secured FastAPI + React dashboard with SSE live events |

## Layer cake

```text
┌────────────────────────────────────────────────────────────┐
│  React SPA (Vite) — ui/dist served under /baselithbot/ui   │
└───────────────▲────────────────────────────────────────────┘
                │ REST + SSE
┌───────────────┴────────────────────────────────────────────┐
│  FastAPI router  (plugins/baselithbot/api/router.py)       │
│    /baselithbot/run /status /inbound /ws/pair /metrics     │
│    /baselithbot/dash/*   (api/ui_api.py + dashboard/routes)│
│    /baselithbot/ui/*     (static bundle, self-diagnosing   │
│                            503 placeholder if unbuilt)      │
└───────────────▲────────────────────────────────────────────┘
                │
┌───────────────┴────────────────────────────────────────────┐
│  BaselithbotPlugin  (plugin.py + _bootstrap.py)             │
│  holds singletons: agent, sessions, channels, skills,       │
│  cron, pairing, canvas, usage, workspaces, run_tracker,     │
│  desktop_run_tracker, inbound_dispatcher, dm_policy,        │
│  model_prefs, slash_state, runtime_config, approvals,       │
│  replay, secret_store, channel_configs, agent_registry      │
└──┬───────┬───────┬──────┬──────┬─────┬──────┬──────────────┘
   ▼       ▼       ▼      ▼      ▼     ▼      ▼
 Agent  Handlers Channels Skills Cron Nodes ComputerUse
   │                                          │
   ▼                                          ▼
 BrowserAgent (browser_agent plugin)     ApprovalGate ◄── dashboard
 + stealth + sanitized JS                     │
 + Set-of-Mark overlay                        ▼
   │                                   OSController / ScopedFS /
   ▼                                   ShellExecutor / AuditLogger
 TaskReplayStore (SQLite)
```

## Observe → Plan → Act loop

`BaselithbotAgent` implements the cognitive loop:

1. **Observe** — `BrowserAgent.get_page_state()` returns `(url,
   screenshot_base64, html_snippet)`.
2. **Plan** — `BrowserAgent.decide_next_action(goal, state, history)`
   returns a typed action (navigate / click / type / scroll / extract /
   done / fail).
3. **Act** — dispatched via sanitized primitives. `EXTRACT` records into
   the per-run store; `DONE`/`FAIL` terminates; hitting `max_steps` returns
   a partial result.

Every step emits an `on_progress` callback consumed by the run tracker and
the dashboard event bus, so the UI renders real-time step reasoning,
screenshots, and extracted data — see [Run Task](../guide/run-task.md) and
[Replay](../guide/replay.md).

`BaselithbotAgent` extends `core.lifecycle.mixins.LifecycleMixin`:

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

`execute()` refuses work unless `state == READY`. The health check exposes
`backend_started` + `stealth_enabled` through the framework health
aggregator. The singleton browser agent starts lazily on first use
(`get_or_start_agent()`) so an unused deployment never launches Chromium.

## Plugin registration

`BaselithbotPlugin` subclasses `core.plugins.AgentPlugin` and
`core.plugins.RouterPlugin`. During BaselithCore app startup, the registry:

1. Calls `initialize(config)` with the plugin's block from
   `configs/plugins.yaml`.
2. Calls `create_router()` → mounted under **`/baselithbot`** (not
   `/api/baselithbot` — `get_router_prefix()` overrides the default so the
   bundled React dashboard is reachable at a human-friendly URL).
3. Merges `get_mcp_tools()` (aggregated by `_mcp.collect_mcp_tools`) into
   the MCP server.
4. Registers `get_intent_patterns()` — the `baselithbot_browse` intent
   (priority 110) routes matching user utterances to
   `BaselithbotFlowHandler.handle_browse`.
5. Calls `shutdown()` on app teardown — stops the agent, the cron
   scheduler, and every live channel adapter.

## Module map

| Area | Package | Role |
|---|---|---|
| Registration | [`plugin.py`](../../plugin.py), [`_bootstrap.py`](../../_bootstrap.py), [`_mcp.py`](../../_mcp.py) | Entry point + singleton holder; init helpers extracted so `plugin.py` stays under the 500 LOC cap; MCP tool aggregation |
| Browser | [`browser/`](../../browser/) | `agent.py` (cognitive loop), `tools.py` (7 MCP tools), `stealth.py`, `som.py` (Set-of-Mark), `vision_failover.py` |
| Computer Use | [`computer_use/`](../../computer_use/) | `config.py`, `os_control.py`, `desktop_vision.py`, `shell_exec.py`, `filesystem.py`, `process_manager.py`, `tools.py` + `extra_tools.py`, `desktop_lane.py`, `spotify_control.py` |
| Control plane | [`control/`](../../control/) | `approvals.py`, `replay.py`, `run_tracker.py`, `tenant.py`, `openclaw_tools.py` |
| Security | [`security/`](../../security/) | `secret_store.py` (Fernet-encrypted provider keys), `redaction.py` |
| Policies | [`policies/`](../../policies/) | `dashboard_auth.py` (bearer guard), `rate_limit.py`, `dm_policy.py`, `host_acl.py` |
| Config | [`config/`](../../config/) | `models.py` (`ModelPreferenceStore`), `runtime.py` (Computer Use / Stealth overlay) |
| OpenClaw parity | [`channels/`](../../channels/) (24 adapters), [`voice/`](../../voice/), [`canvas/`](../../canvas/), [`sessions/`](../../sessions/), [`skills/`](../../skills/), [`nodes/`](../../nodes/), [`gateway/`](../../gateway/), [`cron/`](../../cron/), [`chat/`](../../chat/), [`inbound/`](../../inbound/), [`agents/`](../../agents/) | Subsystems implementing feature parity with OpenClaw |
| API | [`api/`](../../api/) | `router.py` (core routes + UI mount), `ui_api.py`, `handlers.py` |
| Dashboard | [`dashboard/`](../../dashboard/) | `app.py` (route composition), `routes/` (one router per surface) |
| Diagnostics | [`diagnostics/`](../../diagnostics/) | `doctor.py`-style probes |
| UI | [`ui/`](../../ui/) | React 18 + Vite 5 + TypeScript SPA, 20 pages |

## Core reuse

BaselithBot is deliberately **standalone-leaning at the backend** — it has
no hard import of `plugins.auth` anywhere in its Python source — while
still reusing framework machinery where it fits:

| `core` module | How it's used |
|---|---|
| `core.plugins` | `AgentPlugin` + `RouterPlugin` base classes drive registration, lifecycle, and MCP/intent wiring. |
| `core.lifecycle.mixins` / `.protocols` | `BaselithbotAgent` extends `LifecycleMixin`; typed `AgentState` for the state machine above. |
| `core.mcp.server` | `MCPServer` type used when building browser MCP tool definitions. |
| `core.observability.logging` | `get_logger` — every structured log line across the plugin. |
| `core.services.sanitization` | `InputSanitizer` — sanitizes user-supplied arguments to `baselithbot_eval_js_safe` before they reach the whitelisted-JS evaluator. |
| `core.services.vision` | `VisionService` contract + models — `FailoverVisionService` wraps it with the operator's failover chain (see [Models](../guide/models.md)). |
| `core.di.container` | `ServiceRegistry` — used *only* to detect whether a central `AuthManager` is registered (see [Multi-tenancy](tenancy.md)); absence is treated as "no central auth," not an error. |
| `core.auth` | Optional, read-only: `AuthManager.authenticate()` resolves a caller's `tenant_id` for the replay store when central auth is active. Never imported for route guarding — see [Security & RBAC](security.md). |
| `core.config` | Provider API keys (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GOOGLE_API_KEY`) resolved through `core.config.services` rather than reading `os.environ` directly. |
| `core.cli.__main__` | `cli.py` registers the `baselith baselithbot …` command tree. |

Subsystems this plugin does **not** reach into `core` for: memory
(STM/MTM/LTM), orchestration beyond the single intent-pattern bridge,
reasoning/world_model/swarm/planning/meta, evaluation, and personas — the
plugin's cognitive loop is self-contained (`BaselithbotAgent`), and its
sub-agent routing is its own lightweight `AgentRegistry`, not the framework
orchestrator's multi-agent primitives.

## Composition

BaselithBot composes [`plugins/browser_agent`](../../../browser_agent/)
(`plugin_dependencies: {browser_agent: '>=0.1.0'}` in the manifest) for the
underlying Playwright driver; everything else (stealth, Computer Use,
channels, canvas, voice, sessions, skills, cron, nodes, gateway) is native
to this plugin.
