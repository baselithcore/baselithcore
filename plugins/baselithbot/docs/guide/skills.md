# Skills

The OpenClaw-compatible capability registry: what BaselithBot can inject
into an agent's prompt, across three scopes.

## Scopes

| Scope | Meaning |
|---|---|
| `bundled` | Shipped with the plugin. |
| `managed` | Installed from the remote **ClawHub** catalog. |
| `workspace` | Authored per-project, discovered from the active workspace on disk. |

Each skill can export `AGENTS.md` / `SOUL.md` / `TOOLS.md`; the injection
bundle is assembled by the `skills_inject` MCP tool for the orchestrator's
prompt builder.

## What the page does

- **Stat strip** — installed / bundled / managed / workspace counts.
- **Skill author** — create a new workspace skill from the browser (name,
  description, instructions), validated on save; the result reports a
  validation status (`valid` / `invalid`) and the created spec.
- **Installed skills** — searchable, sortable (name / scope / version) list
  grouped by scope, with a detail view and remove action (removing a
  `managed` or custom `workspace` skill also deletes its on-disk bundle;
  removing others just drops the registry entry).
- **Workspace validation panel** — surfaces any workspace skill that failed
  discovery/parsing.
- **Registry quick install + ClawHub catalog** — browse the remote catalog,
  install a skill by name, or **sync** the whole catalog. Shows the
  configured ClawHub base URL, install directory, and whether an auth token
  is set.
- **Rescan workspace** — drops previously registered workspace skills and
  re-discovers them from disk (useful after editing files outside the UI).

## Backend

`GET /dash/skills`, `GET /dash/skills/workspace/validate`,
`GET /dash/skills/clawhub` (status) / `GET /dash/skills/clawhub/catalog`,
`PUT /dash/skills/clawhub` (🔒, reconfigure client),
`POST /dash/skills/clawhub/sync` (🔒), `POST /dash/skills/clawhub/install/{name}`
(🔒), `POST /dash/skills/workspace` (🔒, author), `POST /dash/skills/rescan`
(🔒), `DELETE /dash/skills/{name}` (🔒).
