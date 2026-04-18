# Baselithbot Documentation

Version: `1.0.0` · Readiness: `alpha` · License: MIT
Framework: BaselithCore (Python 3.10–3.12, FastAPI, Pydantic, async I/O)

Baselithbot is the OpenClaw-parity agentic platform shipped as a single
BaselithCore plugin. It bundles autonomous browser navigation, OS-level
Computer Use with human-in-the-loop gating, a time-travel replay store,
Set-of-Mark vision annotation, a multi-channel inbox, a Live Canvas
(A2UI) surface, voice output, sandboxed sessions, a skills registry, a
cron scheduler, node pairing, a remote gateway, a cost/usage ledger, a
React dashboard, and an extensive MCP tool surface — all composable
from one plugin mount point (`/baselithbot`).

## Document index

| # | Document | Scope |
|---|----------|-------|
| 1 | [architecture.md](./architecture.md) | Overview, design goals, Observe→Plan→Act loop, module map |
| 2 | [installation.md](./installation.md) | Dependencies, Chromium setup, UI build, plugin enablement |
| 3 | [configuration.md](./configuration.md) | `plugins.yaml` keys, `StealthConfig`, `ComputerUseConfig`, runtime overlay, env vars |
| 4 | [http-api.md](./http-api.md) | Core routes, WebSocket pairing, inbound webhooks, static UI |
| 5 | [dashboard.md](./dashboard.md) | React SPA pages (20) and dashboard REST+SSE API |
| 6 | [mcp-tools.md](./mcp-tools.md) | 37+ MCP tools — browser, Computer Use, OpenClaw, extras, Set-of-Mark |
| 7 | [subsystems.md](./subsystems.md) | Channels, sessions, skills, cron, nodes, gateway, voice, canvas |
| 8 | [computer-use.md](./computer-use.md) | OS-level safety model, capability gates, audit, human-in-loop approval |
| 9 | [approvals.md](./approvals.md) | `ApprovalGate` model, routes, UI, timeout semantics |
| 10 | [replay.md](./replay.md) | `TaskReplayStore` schema, retention cron, scrubber UI |
| 11 | [set-of-mark.md](./set-of-mark.md) | SoM overlay module + MCP tool for VLM-accurate clicks |
| 12 | [models.md](./models.md) | LLM/Vision prefs, failover chain, persistence |
| 13 | [security.md](./security.md) | Auth, rate limits, inbound hardening, security headers |
| 14 | [observability.md](./observability.md) | Prometheus, audit log, structured logs, SSE bus |
| 15 | [cli-sdk.md](./cli-sdk.md) | CLI reference, Python SDK usage, orchestrator intents |
| 16 | [operations.md](./operations.md) | Testing, deployment recipes, troubleshooting, roadmap |
| 17 | [publishing.md](./publishing.md) | Standalone-repo extraction + Baselith Marketplace submission |

## Quick links

- Manifest: [`../manifest.yaml`](../manifest.yaml)
- Catalog entry: [`../catalog-info.yaml`](../catalog-info.yaml)
- Repository `CLAUDE.md`: [`../../../CLAUDE.md`](../../../CLAUDE.md)
- Architecture gate: [`../../../scripts/check_architecture_boundaries.py`](../../../scripts/check_architecture_boundaries.py)
- Strict-typing gate: [`../../../scripts/check_official_plugin_typing.py`](../../../scripts/check_official_plugin_typing.py)

## Design invariants (Sacred Core rule)

- Lives entirely under [`plugins/`](../../). Never imports `core → plugins`.
- Composes [`plugins/browser_agent`](../../browser_agent/) for Playwright.
- Every module ≤ 500 LOC. Bootstrap helpers extracted to [`_bootstrap.py`](../_bootstrap.py).
- All I/O async; every config object Pydantic.
- Computer Use **off by default**. Each sub-capability gates independently.
- Every privileged action written to JSON-Lines audit log.
- Human-in-the-loop approval available per capability via `require_approval_for`.
- Every recorded task step persisted to SQLite (`replay.sqlite`) for time-travel debug.
- Every dashboard write endpoint rate-limited + bearer-token-guarded + logged.
- State files excluded from git: `plugins/*/.state/` in `.gitignore`.
