# Doctor

A one-page environment, dependency, and live-plugin-state probe — the
dashboard surface for `baselith doctor` / `baselith baselithbot status`.

## What the page does

- **Platform** — OS, Python version (checked against the 3.12+ minimum),
  architecture.
- **Python dependencies** — Playwright install + Chromium availability,
  `pyautogui`, `mss`, `Pillow` (each a boolean pass/fail).
- **System binaries** — Docker daemon reachability (used for optional
  per-session sandboxing) and the Tailscale CLI on `$PATH`.
- **Agent / plugin runtime panel** — live state pulled straight from the
  running `BaselithbotPlugin` (agent state, stealth enabled, backend
  started), when available.
- **Re-run** button to refresh on demand; auto-refreshes every 30s.

## Backend

`GET /dash/doctor` — unauthenticated read, runs the same async probe used
by the `baselithbot_doctor` MCP tool and the CLI.

## When to use it

Run Doctor first whenever a capability silently "doesn't work" —
Computer Use failing with no clear error, mouse/keyboard actions no-op on
macOS, or a Docker-sandboxed session falling back to in-process execution
are all diagnosable here before digging into
[Computer Use](computer-use.md) or [Audit Log](audit-log.md). See the
[Operations runbook](../operations/runbook.md) for the fixes behind each
common failure.
