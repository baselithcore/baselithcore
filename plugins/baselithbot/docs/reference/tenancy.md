# Multi-tenancy

BaselithBot's `manifest.yaml` declares no `tenancy` key, so it inherits the
framework default (`tenancy: shared`) — but the plugin's actual behavior is
closer to a **single-operator control plane** than a multi-tenant SaaS
surface, with one deliberate exception. Read this page before assuming
either "fully isolated" or "fully shared."

## What is *not* tenant-scoped

Nearly every subsystem is a **process-wide singleton**, by design: one
browser agent, one channel registry, one skills registry, one cron
scheduler, one canvas surface, one usage ledger, one model-preference
store, one Computer Use / Stealth policy overlay, one approval gate, one
provider-key vault. These represent *one deployment's* automation stack —
sessions, workspaces, channels, and cron jobs are shared by every caller of
that deployment's dashboard, the same way a single Slack bot or CI runner
is shared by everyone who can reach it. There is no per-user partitioning
of this state, and no code path resolves a tenant before reading or writing
it.

This matches the **single-owner / config-pinned plugin** exemption from the
platform's multi-tenancy convention: one BaselithBot deployment is
generally one owner's automation surface, gated by the dashboard bearer
token (see [Security & RBAC](security.md)), not a shared app serving
isolated tenants.

## The one exception: run replay history

`control/tenant.py` (`tenant_from_request`) resolves a tenant id for two
routes — `POST /baselithbot/run` and the `/dash/replay/*` routes — and
`control/replay.py` (`TaskReplayStore`) scopes every row in its SQLite
`runs` table by that `tenant_id` (default `'default'`). This means: if
multiple identities share one BaselithBot deployment behind central auth,
each identity's run history and screenshots in [Replay](../guide/replay.md)
are isolated from each other's, even though every other subsystem on that
same deployment is shared.

### How the tenant is resolved — fail closed

```python
async def tenant_from_request(request) -> str | None:
    manager = _central_auth_manager()  # ServiceRegistry.get(AuthManager) or None
    if manager is None:
        return "default"          # no central auth → single-tenant deployment
    header = request.headers.get("authorization")
    if not header:
        return None               # central auth active, no credential → fail closed
    user = await manager.authenticate(header)
    if user is None or not user.is_authenticated:
        return None               # invalid/expired/transient → never leak
    return user.tenant_id
```

Two states matter:

- **No central `auth` plugin registered** — `tenant_id` is always
  `"default"`. There is nothing to isolate, so this is correct, not a
  fallback-to-insecure.
- **Central `auth` is active** — a caller with no/invalid/expired bearer
  resolves to `None`, and callers **must** treat `None` as "no tenant":
  refuse the read/write (401 on writes, empty/404 on reads) rather than
  falling back to `"default"`, which is itself a valid tenant an
  unauthenticated caller has no business reading.

This deliberately does **not** collapse "auth failed" into `"default"` —
the old behavior this replaced was exactly that bug, letting an
unauthenticated caller read the default tenant's replay history.

### Why this is a read of the *central auth token*, not the dashboard token

BaselithBot authenticates its write endpoints with its own
`DashboardAuth` bearer (`BASELITHBOT_DASHBOARD_TOKEN`), which is orthogonal
to central auth and carries no identity — so the tenant context var is
never bound on BaselithBot's own routes the way it is on a plugin whose
routes sit behind `require_auth`/`require_roles`. `tenant_from_request`
compensates by decoding the request's `Authorization` bearer **itself**,
directly against the central `AuthManager` (via `core.di.container.ServiceRegistry`),
independent of whatever `DashboardAuth` decided about the same header. In
practice a caller presents one bearer that both gates the write
(`DashboardAuth`) and — only for `/run` and `/dash/replay/*` — identifies
the tenant.

## What this means operationally

| Deployment shape | Effect |
|---|---|
| Single operator, no central auth | Everything, including replay, is one implicit `"default"` tenant. Nothing to configure. |
| Single operator, central auth present but unused for this plugin | Same as above — replay stays `"default"` unless callers present a central bearer on `/run`/`/dash/replay/*`. |
| Multiple identities sharing one deployment behind central auth | Replay history/screenshots isolate per identity's `tenant_id`; every other surface (sessions, channels, skills, cron, canvas, models, Computer Use policy, provider keys) remains shared across all of them. |

If you need full per-tenant isolation of *every* surface (not just replay),
that is a deliberate scope decision this plugin does not make today — run
separate deployments per tenant instead, each with its own dashboard token
and `.state/` directory.
