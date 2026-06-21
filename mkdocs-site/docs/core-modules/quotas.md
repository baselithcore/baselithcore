---
title: Usage Quotas
description: Persistent per-key request budgets over calendar windows
---

The `core/quotas` module enforces **persistent usage budgets** over calendar
windows — daily and monthly — at two independent scopes: **per identity** (API
key / user) and **per tenant** (aggregate across all the tenant's members). It is
a distinct layer from the two existing controls:

| Control | Scope | Window |
| ------- | ----- | ------ |
| Rate limiting | requests/identity | rolling minute |
| Cost control | tokens/request | single request |
| **Quotas (identity)** | **requests/identity** | **calendar day & month** |
| **Quotas (tenant)** | **requests/tenant (aggregate)** | **calendar day & month** |

Opt-in via `QUOTAS_ENABLED`; default-off and a no-op until configured.

## Automatic enforcement

`QuotaMiddleware` (`core/middleware/quota.py`, pure ASGI, registered in the app
factory) enforces both scopes transparently. On every **authenticated** request it
consumes one unit from the caller's identity budget *and* their tenant's aggregate
budget; if either window is exhausted it returns `429` (with `Retry-After: 60`)
before the route runs. It self-authenticates from the bearer token, so it does not
depend on its position in the stack. A complete no-op unless `QUOTAS_ENABLED`;
unauthenticated requests are not quota-scoped and pass through.

## How it works

Counters are keyed by `identity:window:period` where `period` embeds the
calendar date (`20260617` / `202606`), so a counter resets naturally when the
window rolls over. Enforcement is **check-then-consume**: both windows are read
first and the request is rejected *without consuming* if either would exceed, so
a rejected request never burns budget.

```python
from core.quotas import get_quota_manager, QuotaExceededError

manager = get_quota_manager()
try:
    status = await manager.check_and_consume(api_key_id, cost=1)
    # status.windows["daily"].remaining → budget left today
except QuotaExceededError as e:
    # surfaced by the API as 429 quota_exceeded
    ...
```

Tenant budgets use a parallel API keyed under a `tenant:` namespace, so identity
and tenant counters never collide:

```python
await manager.check_and_consume_tenant(tenant_id, cost=1)
status = await manager.peek_tenant(tenant_id)   # report without consuming
```

A `QuotaExceededError` raised inside a request is rendered by the
[error envelope](../api/rest.md#error-envelope) as **429** with code
`quota_exceeded`.

## Limits

Defaults apply to every identity; raise (or lower) them per key at runtime:

```python
from core.config.quotas import set_key_quota, set_tenant_quota

set_key_quota("partner-key-id", daily=100_000, monthly=2_000_000)
set_tenant_quota("tenant-123", daily=1_000_000, monthly=20_000_000)  # tenant plan
```

A limit of `None`/`0` means **unlimited** for that window (so an unset env never
locks everyone out).

## Configuration

| Variable                  | Default  | Description                              |
| ------------------------- | -------- | ---------------------------------------- |
| `QUOTAS_ENABLED`               | `false`  | Master switch                            |
| `QUOTA_DAILY_REQUESTS`         | unlimited| Default daily request budget per identity |
| `QUOTA_MONTHLY_REQUESTS`       | unlimited| Default monthly request budget per identity |
| `QUOTA_TENANT_DAILY_REQUESTS`  | unlimited| Default daily request budget per tenant (aggregate) |
| `QUOTA_TENANT_MONTHLY_REQUESTS`| unlimited| Default monthly request budget per tenant (aggregate) |
| `QUOTA_BACKEND`                | `redis`  | `redis` (shared across workers) or `memory` |

## Storage

`QuotaStore` is a pluggable Protocol. `RedisQuotaStore` (`INCRBY` + `EXPIRE`)
shares counters across workers and bounds stale keys with a TTL anchored to the
window's first request; `InMemoryQuotaStore` is the single-process default and
the fallback when Redis is unavailable.
