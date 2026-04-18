# Dashboard

[← Index](./README.md)

React SPA + dashboard REST + SSE bus. Backend in
[`ui_api.py`](../ui_api.py); frontend in [`ui/`](../ui/).

## 1. React SPA

**Stack** — React 18, Vite 5, TypeScript, vanilla CSS (design tokens, no
Tailwind), `react-chartjs-2`, React Router, TanStack Query, SSE client.

**Pages (20)** — `Overview`, `RunTask`, `Sessions`, `Channels`, `Skills`,
`Crons`, `Nodes`, `Workspaces`, `Agents`, `Canvas`, `Doctor`, `Models`,
`Metrics`, `Logs`, **`ComputerUse`**, **`Stealth`**, **`AuditLog`**,
**`Approvals`**, **`Replay`**, `NotFound` (files under
[`ui/src/pages/`](../ui/src/pages/)).

The 5 bold pages are the operator surfaces added for runtime-configurable
Computer Use, stealth, human-in-the-loop gating, and time-travel replay —
see [approvals.md](./approvals.md), [replay.md](./replay.md),
[computer-use.md](./computer-use.md).

**Shared components** — `Layout`, `Sidebar`, `TopBar`, `Panel`,
`PageHeader`, `StatCard`, `DetailDrawer`, `EmptyState`, `Skeleton`,
`ConfirmProvider`, `ToastProvider`, `DashboardProvider`, `ErrorBoundary`
(under [`ui/src/components/`](../ui/src/components/)).

**Live events** — every page subscribes to `/dash/events/stream` through
`DashboardProvider`, so charts update without polling.

**Accessibility** — `useOverlayA11y` hook for focus trap / ESC handling,
skip-to-content link, Lighthouse A11y audit gated in CI.

**Security UX** — token banner appears when server warns
`baselithbot_dashboard_open`; banner offers paste field that stores token
in `sessionStorage` only.

Build: `cd plugins/baselithbot/ui && npm run build`.
Dev proxy: `npm run dev` (Vite on `:5180`, proxies `/baselithbot/*`).

## 2. Dashboard REST + SSE API

All routes prefixed with `/baselithbot/dash`. Legend: 🔓 read-only, 🔒
bearer-token required.

### 2.1 Overview & sessions

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/dash/overview` | 🔓 | Aggregate snapshot: agent state, counts, inbound stats, usage, cron backend |
| GET | `/dash/sessions` | 🔓 | List sessions |
| POST | `/dash/sessions` | 🔒 30/min | Create session (`title`, `primary`) |
| GET | `/dash/sessions/{sid}/history` | 🔓 | Messages (default 100) |
| POST | `/dash/sessions/{sid}/send` | 🔒 30/min | Send `{role, content, metadata}` |
| POST | `/dash/sessions/{sid}/reset` | 🔒 | Clear history |
| DELETE | `/dash/sessions/{sid}` | 🔒 20/min | Delete session |

### 2.2 Channels / skills / crons

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/dash/channels` | 🔓 | Known/live channels + inbound counters |
| GET | `/dash/skills?scope=…` | 🔓 | Skills filtered by scope (`bundled`/`managed`/`workspace`) |
| GET | `/dash/crons` | 🔓 | Cron backend + scheduled jobs |
| POST | `/dash/crons/{name}/remove` | 🔒 20/min | Remove a cron job |

### 2.3 Nodes / gateway

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/dash/nodes` | 🔓 | Paired nodes + pairing status |
| POST | `/dash/nodes/token` | 🔒 5/min | Issue pairing token (optional `platform`) |
| DELETE | `/dash/nodes/{node_id}` | 🔒 20/min | Revoke paired node |

### 2.4 Operational

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/dash/doctor` | 🔓 | Environment/dependency probe |
| GET | `/dash/canvas` | 🔓 | `CanvasSurface.snapshot()` |
| GET | `/dash/usage/summary` | 🔓 | Cost totals + by-model breakdown |
| GET | `/dash/usage/recent?limit=N` | 🔓 | Last N usage events |
| GET | `/dash/run-task/latest` | 🔓 | Latest run state |
| GET | `/dash/run-task/recent?limit=N` | 🔓 | Recent run states (default 8) |
| GET | `/dash/run-task/{run_id}` | 🔓 | Specific run state |
| GET | `/dash/agents` | 🔓 | Sub-agent registry |
| GET | `/dash/workspaces` | 🔓 | Workspace runtime summaries |

### 2.5 Models

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/dash/models` | 🔓 | Current prefs + catalog |
| PUT | `/dash/models` | 🔒 5/min | Update `ModelPreferences` (validated against catalog) |

### 2.6 Computer Use + Stealth runtime overlay

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/dash/computer-use` | 🔓 | Effective `ComputerUseConfig` (boot + overlay) |
| PUT | `/dash/computer-use` | 🔒 5/min | Persist runtime overlay + invalidate agent |
| GET | `/dash/stealth` | 🔓 | Effective `StealthConfig` |
| PUT | `/dash/stealth` | 🔒 5/min | Persist overlay + invalidate agent |

### 2.7 Audit log tail

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/dash/audit-log?limit=N&action=X` | 🔓 | Tail the JSONL audit log; filter by `action` substring; response includes `scanned_rows`, `status_counts`, `action_counts`, `oldest_ts`, `newest_ts`. Returns `configured=false` when `audit_log_path` is unset. |

### 2.8 Approvals (human-in-the-loop)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/dash/approvals` | 🔓 | Pending + last-50 resolved approval requests |
| POST | `/dash/approvals/{id}/approve` | 🔒 5/min | Resolve pending request as `approved` (optional `reason`) |
| POST | `/dash/approvals/{id}/deny` | 🔒 5/min | Resolve pending request as `denied` |

### 2.9 Replay (time-travel debug)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/dash/replay/runs?limit=N` | 🔓 | List persisted runs with status + step count |
| GET | `/dash/replay/runs/{run_id}` | 🔓 | Full run + every step (screenshot, reasoning, URL, extracted) |

### 2.10 Metrics + events

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/dash/metrics/prometheus` | 🔓 | Prometheus text (JSON-wrapped) |
| GET | `/dash/events/recent?limit=N` | 🔓 | Replay last N dashboard events (default 50) |
| GET | `/dash/events/stream` | 🔓 | Server-Sent Events stream (live) |

## 3. `DashboardEventBus` event catalog

Published types:

- `run.started`, `run.step`, `run.completed`, `run.failed`
- `session.created`, `session.message`, `session.reset`, `session.deleted`
- `skill.clawhub_configured`, `skill.clawhub_synced`, `skill.installed`,
  `skill.rescanned`, `skill.removed`
- `cron.custom_registered`, `cron.custom_updated`, `cron.removed`,
  `cron.triggered`, `cron.enabled`, `cron.paused`, `cron.interval_updated`
- `agent.custom_registered`, `agent.custom_updated`, `agent.custom_deleted`,
  `agent.dispatched`
- `channel.config_updated`, `channel.config_deleted`, `channel.started`,
  `channel.stopped`, `channel.inbound`
- `canvas.rendered`, `canvas.cleared`, `canvas.action`
- `workspace.created`, `workspace.updated`, `workspace.deleted`
- `models.updated`
- `provider_keys.updated`, `provider_keys.deleted`
- `node.token_issued`, `node.revoked`
- `computer_use.updated`, `stealth.updated`
- `approval.pending`, `approval.resolved`, `approval.approved`, `approval.denied`

Bus properties:

- Process-wide singleton (`get_event_bus()`).
- Bounded 200-event ring buffer for history replay.
- 256-deep per-subscriber queue; slow consumers dropped (non-fatal).
- Initial SSE frame: `": connected\n\n"` (comment heartbeat).

## 4. SSE framing

Each event is dual-emitted on two channels so that both type-specific
listeners and wildcard consumers (Live Logs UI) receive every frame:

```text
event: run.step
data: {"type":"run.step","ts":1724512345.12,"payload":{...}}

data: {"type":"run.step","ts":1724512345.12,"payload":{...}}
```

Consumer side (TypeScript) — wildcard via default `onmessage`:

```ts
const es = new EventSource("/baselithbot/dash/events/stream");
es.onmessage = (e) => {
  const { type, payload } = JSON.parse(e.data);
  if (type === "run.step") {
    // render step...
  }
};
```

Type-specific listener still works for external consumers:

```ts
es.addEventListener("run.step", (e) => {
  const { payload } = JSON.parse((e as MessageEvent).data);
  // render step...
});
```

Pick one style per consumer — mixing both will deliver each event twice.

Proxy requirements (nginx example in [operations.md](./operations.md)):
disable buffering (`X-Accel-Buffering: no`), keep `Cache-Control: no-cache`,
long `proxy_read_timeout`.
