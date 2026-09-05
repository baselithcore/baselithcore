# Computer Use

The **policy editor** for OS-level Computer Use — capability flags, the
shell allowlist, filesystem scoping, and human-in-the-loop requirements.
(For the interactive tool runner, see [Desktop Task](desktop-task.md).)

## What the page does

- **Hero + stats** — whether Computer Use is enabled at all, and a summary
  of which capabilities are currently allowed.
- **Capability matrix** — toggle `allow_mouse`, `allow_keyboard`,
  `allow_screenshot`, `allow_shell`, `allow_filesystem` independently. The
  master `enabled` switch gates all of them — flipping it off makes every
  Computer Use tool return `{"status": "denied"}` immediately, without
  touching the OS.
- **Guardrails** — shell command allowlist editor (one command per line,
  matched by first-token exact or space-prefix), shell timeout
  (1–600s), filesystem root path, per-write byte cap, and the audit log
  path.
- **Risk review** — a human-readable summary of what the current draft
  policy would allow, before saving.
- **Require-approval selector** — which capabilities (`mouse`, `keyboard`,
  `screenshot`, `shell`, `filesystem`) must pass through the
  [Approvals](approvals.md) queue, and the approval timeout
  (1–3600s, auto-deny on expiry).
- **Tool surface** — the resulting list of MCP tool names this policy
  currently exposes.

## Backend

`GET /dash/computer-use` (effective config = boot config + runtime
overlay), `PUT /dash/computer-use` (🔒) validates, persists the overlay to
`plugins/baselithbot/.state/runtime_config.json`, **invalidates the cached
agent** so the next run rebuilds with the new guardrails, and emits
`computer_use.updated` on the SSE bus.

## Safety model

Every gate is layered and none of them raise past the tool boundary —
denials and errors both come back as status envelopes. Full detail,
including the shell allowlist semantics and audit-log format, is in
[Security & RBAC](../reference/security.md).
