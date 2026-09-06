# Desktop Task

The **interactive runner** for Computer Use — invoke tools directly or let
a goal-driven `DesktopAgent` chain them, gated by whatever policy is set on
the [Computer Use](computer-use.md) page.

## What the page does

- **Tool catalog** — every Computer Use tool the current policy allows
  (mouse, keyboard, screenshot, shell, filesystem), with a policy readiness
  checklist explaining what's missing if a capability is greyed out (e.g.
  "enable `allow_shell` and populate the allowlist").
- **Goal run** — describe a desktop goal in natural language; a
  `DesktopAgent` (built from the current vision service + tool map +
  policy) drives it, separate from the browser `RunTracker` used by
  [Run Task](run-task.md). A **cancel** action stops the run at the next
  loop iteration.
- **Direct tool sections** — Screen/Pointer, Keyboard, Shell, Filesystem —
  invoke a single tool without a full goal-driven run, useful for quick
  probes or debugging a policy change.
- **Inspector / history** — recent desktop runs with their outcome and an
  expandable run log.

## Backend

`GET /dash/desktop/tools` (catalog + effective policy),
`POST /dash/desktop/tools/{tool_name}` (🔒, direct single-tool invocation),
`POST /dash/desktop/task` (🔒, goal-driven run),
`POST /dash/desktop/task/{run_id}/cancel` (🔒),
`GET /dash/desktop/task/latest` / `/recent` / `/{run_id}`.

## Notes

Desktop runs use a separate serialized **lane** (one active run at a time
per host) from browser runs, so a desktop task and a browser task never
race for the same OS-level resources. Every privileged invocation — direct
or goal-driven — goes through the same [Approvals](approvals.md) gate and
[audit log](audit-log.md) as any other Computer Use action.
