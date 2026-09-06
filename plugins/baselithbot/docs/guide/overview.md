# Overview dashboard

The landing page (`/baselithbot/`, route `/`) — the morning-briefing snapshot
of the whole control plane.

## What it shows

- **Agent state** — `uninitialized / starting / ready / stopping / stopped`,
  whether the backend has started, and whether stealth is on.
- **Sessions** — total bounded conversation histories, workspace count, agent
  count.
- **Channels** — `live / registered` adapters.
- **Paired nodes** and the active cron backend label.
- **Usage (buffer)** — total tokens, cost (USD), average latency, sourced
  from the [`UsageLedger`](../reference/architecture.md).
- **Provider keys** — `configured / allowed` count from the encrypted
  provider-key vault (see [Models](models.md)).
- A **usage trend chart** (tokens + latency over the last 120 recorded
  events) and a **live event feed** — both driven by the same dashboard SSE
  bus used across every page.
- **Inbound events** by channel and a **subsystem roster** (sessions,
  agents, skills, cron jobs, paired nodes, registered channels, workspaces,
  canvas widgets).

## Data sources

`GET /baselithbot/dash/overview` (unauthenticated read) aggregates counts
across every subsystem singleton the plugin holds; `GET /dash/usage/recent`
feeds the chart; `GET /dash/events/stream` (SSE) feeds the live feed and
keeps every stat current without polling.

## When to use it

Use Overview to answer "is the agent healthy and what happened recently"
before drilling into a specific surface — a spike in inbound events points
to [Channels](channels.md), a cost jump points to [Metrics](metrics.md), and
an idle agent state with `backend_started: false` means no run has started
the singleton browser agent yet (it starts lazily on first use).
