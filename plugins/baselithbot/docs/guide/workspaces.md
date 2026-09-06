# Workspaces

Multi-workspace management — isolate configuration and skill discovery
across multiple projects/deployments served by one BaselithBot instance.

## What the page does

- **Roster** — every workspace with its primary flag, description, and how
  many channels it overrides from the global default.
- **Create** a workspace (name, description, mark as primary).
- **Update** an existing workspace's description/overrides.
- **Delete** a workspace (with confirmation).

A `default` workspace is auto-created on first boot if none exists.

## Backend

`GET /dash/workspaces` (also surfaced via `GET /dash/overview`'s roster),
`POST /dash/workspaces` (🔒), `PUT /dash/workspaces/{name}` (🔒),
`DELETE /dash/workspaces/{name}` (🔒).

## Notes

Workspaces are also the discovery root for `workspace`-scoped
[skills](skills.md) — `WorkspaceManager` persists to
`plugins/baselithbot/.state/workspaces.json`. The `baselithbot_workspace_create`
/ `_list` / `_activate` / `_destroy` MCP tools operate on the same registry.
