---
title: Admin Console
description: The built-in web console for chat, health, and webhook management
---

BaselithCore ships a first-party web **Console** at `/console` — a dependency-free
single-page app served by the framework itself (no build step, no external
scripts; it satisfies the strict runtime CSP `script-src 'self'`).

Open it at `http://localhost:8000/console`.

## Views

| View         | What it does                                                        |
| ------------ | ------------------------------------------------------------------- |
| **Chat**     | Query the agent with live streaming (falls back to non-streaming).  |
| **Overview** | Liveness/readiness probes and (with an admin key) system status.    |
| **Webhooks** | Register, list, and delete webhook endpoints; inspect and replay deliveries. |

## Authentication

Enter an API key in the **Connection** panel (sidebar). It is kept in the
browser's `sessionStorage` — scoped to the tab and discarded when it closes —
and sent as the `X-API-Key` header on every request; once stored it is never
written back into the DOM.
The console surfaces auth failures clearly — a `403 insufficient_scope` means the
key is authenticated but lacks the [capability scope](../core-modules/auth.md#capability-scopes-fine-grained-authorization)
the action needs (e.g. `webhooks:write`).

## Webhooks management

The Webhooks view is backed by the [webhook management API](../core-modules/webhooks.md)
and requires `WEBHOOKS_ENABLED=true`. You can:

- **Register** an endpoint (URL + event types). The signing **secret is shown
  once** on creation — copy it then.
- **Delete** endpoints (scoped to your tenant).
- **Inspect deliveries** and **replay** failed ones.

## Architecture

The console is plain ES modules under `core/static/frontend/js/` (an `api`
client, a tiny DOM helper, and one module per view), served via the `/static`
mount. The routes live in the `api_routers` plugin
(`plugins/api_routers/console.py`): `/console` and `/console/{full_path:path}`
both return `core/static/frontend/index.html`, so client-side routing works on
refresh. `core/routers/console.py` is only a backward-compatible shim that
re-exports that router. No bundler is involved — edit a module and reload.
