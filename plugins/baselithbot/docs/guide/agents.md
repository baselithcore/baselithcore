# Agents

The sub-agent registry — built-in system agents plus operator-defined
custom agents, with a dispatch console to test routing.

## What the page does

- **Roster** — every registered agent (`system` vs `custom`), with totals
  (`all / custom / system`).
- **Details panel** for the selected agent.
- **Create** a custom agent from a form (name, routing description,
  behavior) — persisted and restored on startup.
- **Update / delete** a custom agent.
- **Dispatch console** — send a free-text query and see which agent the
  registry would route it to, with the dispatch result/status.

## Backend

`GET /dash/agents`, `GET /dash/agents/catalog` (available agent
templates/types), `POST /dash/agents` (🔒, create),
`PUT /dash/agents/{name}` (🔒, update), `DELETE /dash/agents/{name}` (🔒),
`POST /dash/agents/{name}/dispatch` (🔒, test routing).

## Notes

Custom agents persist to
`plugins/baselithbot/.state/custom_agents.json` and are restored
(`bootstrap()`) on plugin startup, the same pattern used by
[Cron](cron.md)'s custom jobs. The `baselithbot_agent_route` MCP tool uses
the same `AgentRegistry` to dispatch programmatically.
