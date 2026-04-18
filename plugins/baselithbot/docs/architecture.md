# Architecture

[← Index](./README.md)

## 1. Design goals

Baselithbot targets six orthogonal capabilities behind a single plugin:

| Capability | Purpose |
|------------|---------|
| Autonomous browser agent | Goal-driven Observe → Plan → Act loop over Playwright |
| OS-level Computer Use | Mouse / keyboard / screenshot / shell / filesystem primitives |
| Human-in-the-loop gating | Per-capability approval requests bridged to dashboard |
| Time-travel replay | SQLite-persisted per-step screenshots + reasoning |
| Messaging & orchestration | 24 channel adapters, sessions, cron, pairing, skills |
| Operator control plane | Secured FastAPI + React dashboard with SSE live events |

## 2. Layer cake

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
│  BaselithbotPlugin   (plugin.py + _bootstrap.py)           │
│  holds singletons: agent, sessions, channels, skills,      │
│  cron, pairing, canvas, usage, workspaces, run_tracker,    │
│  inbound_dispatcher, dm_policy, model_prefs, slash_state,  │
│  runtime_config, approvals, replay, secret_store           │
└──┬───────┬───────┬──────┬──────┬─────┬──────┬──────────────┘
   │       │       │      │      │     │      │
   ▼       ▼       ▼      ▼      ▼     ▼      ▼
 Agent  Handlers Channels Skills Cron Nodes ComputerUse
   │                                          │
   │                                          ▼
   │                                   ApprovalGate ◄─── dashboard
   │                                          │
   ▼                                          ▼
 BrowserAgent (browser_agent plugin)     OSController /
 + stealth + sanitized JS                ScopedFS / Shell /
 + SoM overlay (som.py)                  Filesystem / Audit
   │
   ▼
 TaskReplayStore (SQLite)
```

## 3. Observe → Plan → Act loop

[`agent.py`](../agent.py) implements the cognitive loop.

1. **Observe** — `BrowserAgent.get_page_state()` returns `(url, screenshot_base64, html_snippet)`.
2. **Plan** — `BrowserAgent.decide_next_action(goal, state, history)` returns a typed `BrowserAction` (navigate / click / type / scroll / extract / done / fail).
3. **Act** — action dispatched via sanitized primitives. `EXTRACT` records into per-run store. `DONE`/`FAIL` terminates. `MAX_STEPS` returns partial result.

Per step, agent emits `on_progress` callback consumed by
[`run_tracker.py`](../run_tracker.py) and `DashboardEventBus` so UI renders
real-time step reasoning, screenshots, extracted data.

## 4. Plugin registration

`BaselithbotPlugin` subclasses both `AgentPlugin` and `RouterPlugin`
([`core/plugins`](../../../core/plugins/)). During BaselithCore app startup,
the registry:

1. Calls `initialize(config)` with the block from `configs/plugins.yaml`.
2. Calls `create_router()` → mounted under **`/baselithbot`** (not
   `/api/baselithbot`, override via `get_router_prefix`) so UI reachable at
   human-friendly URL.
3. Merges `get_mcp_tools()` into the MCP server.
4. Registers `get_intent_patterns()` — `baselithbot_browse` intent
   (priority 110) routes matching user utterances to `handle_browse`.
5. Calls `shutdown()` on app teardown — stops agent, cron scheduler, every
   live channel.

Singleton browser agent lazily started on first use via
`get_or_start_agent()` to avoid launching Chromium on unused deploys.

## 5. Module map

### 5.1 Browser layer

| File | Role |
|------|------|
| [`plugin.py`](../plugin.py) | Registration entry point; holds singletons (<500 LOC) |
| [`_bootstrap.py`](../_bootstrap.py) | Extracted init helpers: agents, cron jobs, bundled skills, workspace skills, channel autostart, model prefs |
| [`agent.py`](../agent.py) | `BaselithbotAgent` cognitive loop over BrowserAgent |
| [`handlers.py`](../handlers.py) | `BaselithbotFlowHandler.handle_browse` intent bridge |
| [`router.py`](../router.py) | FastAPI router (`/run`, `/status`, `/inbound`, `/ws/pair`, `/metrics`, UI mount). `/run` persists per-step snapshots into the replay store. |
| [`tools.py`](../tools.py) | 7 browser MCP tools |
| [`types.py`](../types.py) | `StealthConfig`, `BaselithbotTask`, `BaselithbotResult` |
| [`stealth.py`](../stealth.py) | WebDriver mask + UA rotation + locale spoof |
| [`som.py`](../som.py) | Set-of-Mark DOM overlay + `baselithbot_som_annotate` MCP tool |
| [`js_whitelist.py`](../js_whitelist.py) | `ALLOWED_SNIPPETS` for `eval_js_safe` |
| [`cli.py`](../cli.py) | `baselith baselithbot {run, status, onboard}` |

### 5.2 Computer Use layer

| File | Role |
|------|------|
| [`computer_use.py`](../computer_use.py) | `ComputerUseConfig` (incl. `require_approval_for`, `approval_timeout_seconds`), `AuditLogger`, `ComputerUseError` |
| [`approvals.py`](../approvals.py) | `ApprovalGate` + `ApprovalRequest` + `ApprovalStatus` — asyncio-native human-in-loop wait |
| [`os_control.py`](../os_control.py) | Mouse/keyboard via `pyautogui`, gated through `ApprovalGate` |
| [`desktop_vision.py`](../desktop_vision.py) | Screenshots via `mss` + `Pillow` |
| [`shell_exec.py`](../shell_exec.py) | Allowlisted subprocess (`shell=False`, timeout), gated |
| [`filesystem.py`](../filesystem.py) | `ScopedFileSystem` — `..`-blocked, byte-cap, gated on write |
| [`process_manager.py`](../process_manager.py) | `psutil`-based process inspection |
| [`computer_tools.py`](../computer_tools.py) | 12 Computer Use MCP tools, optional `ApprovalGate` wired in |
| [`secret_redaction.py`](../secret_redaction.py) | Scrubs tokens/keys from audit log |
| [`runtime_config.py`](../runtime_config.py) | JSON-backed overlay for `computer_use` + `stealth` (dashboard-mutable) |
| [`secret_store.py`](../secret_store.py) | Encrypted (Fernet) provider-key store under `<state>/provider_keys.enc.json` |
| [`replay.py`](../replay.py) | SQLite `TaskReplayStore` — per-step screenshot + reasoning + URL persistence |

### 5.3 OpenClaw-parity layer

| Subsystem | Files |
|-----------|-------|
| Channels | [`channels/`](../channels/) (24 adapters + base + registry + bootstrap) |
| Voice / Audio | [`voice/`](../voice/) (system TTS + ElevenLabs HTTP + wake state) |
| Canvas / A2UI | [`canvas/`](../canvas/) (`CanvasSurface`, widgets, `A2UIRenderer`) |
| Sessions | [`sessions/`](../sessions/) (`SessionManager`, `DockerSandbox`) |
| Skills | [`skills/`](../skills/) (`SkillRegistry`, ClawHub loader) |
| Nodes | [`nodes/`](../nodes/) (`NodePairing`, WS tokens, command families) |
| Gateway | [`gateway/`](../gateway/) (SSH + Tailscale + provisioning) |
| Integrations | [`integrations/`](../integrations/) (webhooks, Gmail Pub/Sub) |
| Cron | [`cron.py`](../cron.py) (`CronScheduler` async loop) |
| Chat commands | [`chat_commands.py`](../chat_commands.py) (10 slash commands) |
| Doctor | [`doctor.py`](../doctor.py) (dep/env probe) |
| Inbound | [`inbound/`](../inbound/) (payload parsers + dispatcher) |

### 5.4 Control plane

| File | Role |
|------|------|
| [`ui_api.py`](../ui_api.py) | Dashboard REST + SSE router (`/baselithbot/dash/*`) |
| [`dashboard/app.py`](../dashboard/app.py) | Route composition — diagnostics, agents, sessions, registry, channels, run_task, models, provider_keys, workspaces, canvas, **computer_use**, **stealth**, **audit**, **approvals**, **replay**, events |
| [`dashboard/routes/`](../dashboard/routes/) | Per-surface REST routers (one file per group) |
| [`ui/`](../ui/) | React 18 + Vite 5 + TypeScript SPA (20 pages incl. ComputerUse, Stealth, AuditLog, Approvals, Replay) |
| [`policies/dashboard_auth.py`](../policies/dashboard_auth.py) | Bearer-token guard (`BASELITHBOT_DASHBOARD_TOKEN`) |
| [`policies/rate_limit.py`](../policies/rate_limit.py) | Token-bucket rate limiter (per client/route) |
| [`policies/dm_policy.py`](../policies/dm_policy.py) | DM pairing policy for inbound events |
| [`policies/host_acl.py`](../policies/host_acl.py) | Host allowlist |
| [`model_config.py`](../model_config.py) | `ModelPreferences` + persisted `ModelPreferenceStore` |
| [`usage.py`](../usage.py) | `UsageLedger` — LLM spend accumulator |
| [`run_tracker.py`](../run_tracker.py) | Live run state (bounded history) |
| [`metrics.py`](../metrics.py) | Prometheus counters/histograms |
| [`tracing.py`](../tracing.py) | OpenTelemetry shim |

## 6. Lifecycle & state machine

`BaselithbotAgent` extends `core.lifecycle.mixins.LifecycleMixin`.

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

- `execute()` refuses work unless `state == READY`. Returns failing
  `BaselithbotResult` with the current state if called early.
- `_do_health_check()` exposes `backend_started` + `stealth_enabled`
  through framework health aggregator.
- `BaselithbotPlugin.shutdown()` additionally stops cron scheduler and
  every live channel adapter.
