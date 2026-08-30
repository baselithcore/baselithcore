---
title: Usage Quotas
description: Persistent per-key request budgets and per-tenant/per-identity USD cost budgets over calendar windows
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
| **Cost budgets (tenant)** | **USD/tenant (aggregate LLM spend)** | **calendar day & month** |
| **Cost budgets (identity)** | **USD/identity (LLM spend)** | **calendar day & month** |

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

### Batched identity + tenant enforcement

`check_and_consume_pair(identity, tenant_id, cost=1)` enforces the identity **and**
tenant windows together with a single batched read (`get_many` / Redis `MGET`)
followed by a single batched consume (`incr_many` / pipeline). This collapses up
to **8 sequential Redis round trips** per request down to **2**, and consumption
is all-or-nothing: a request rejected on either subject burns **no** budget on
the other. `QuotaMiddleware` calls this method on every authenticated request.

```python
status_pair = await manager.check_and_consume_pair(api_key_id, tenant_id, cost=1)
# raises QuotaExceededError (before consuming) if either window would exceed
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

## Tenant USD cost budgets

Request quotas count *calls*; a tenant on an expensive model can still burn an
arbitrary dollar amount inside its request budget. **Tenant cost budgets** cap
the tenant's **cumulative LLM spend in USD** over the same calendar windows.
They are distinct from the per-run [`LoopLimits.budget_usd`](orchestration.md#loopbudget-iteration-cost-token-cap)
cap: that bounds one request; this bounds the tenant's aggregate spend across
all requests. Configure defaults via `QUOTA_TENANT_DAILY_COST_USD` /
`QUOTA_TENANT_MONTHLY_COST_USD` (default `None` = unlimited; requires
`QUOTAS_ENABLED=true`), and override per tenant at runtime:

```python
from core.config.quotas import get_tenant_cost_overrides, set_tenant_cost_budget

set_tenant_cost_budget("tenant-123", daily=25.0, monthly=400.0)
get_tenant_cost_overrides("tenant-123")   # (25.0, 400.0); (None, None) if unset
```

### Post-paid metering, not check-then-consume

The cost of an LLM call is known only **after** it returns, so unlike request
quotas this cannot be check-then-consume. Instead the manager splits booking
from enforcement:

- `record_tenant_cost(tenant_id, usd)` — books spend against the tenant's
  daily and monthly windows **after** each call. Never raises for budget
  reasons (the money is already spent).
- `check_tenant_cost_budget(tenant_id)` — raises `CostBudgetExceededError`
  when recorded spend has reached a window limit; enforced **before** the
  next call. One over-budget call can therefore complete — the tenant is cut
  off at the next one, which is the correct trade-off for post-paid metering.
- `peek_tenant_cost(tenant_id)` — reports per-window `TenantCostStatus`
  (`limit_usd`, `used_usd`, `remaining_usd`) without consuming anything.

```python
from core.quotas import get_quota_manager
from core.quotas.manager import CostBudgetExceededError

manager = get_quota_manager()
try:
    await manager.check_tenant_cost_budget("tenant-123")
except CostBudgetExceededError as e:
    print(e.tenant_id, e.window, e.limit_usd, e.used_usd)

await manager.record_tenant_cost("tenant-123", 0.0042)
status = await manager.peek_tenant_cost("tenant-123")
print(status.windows["daily"].remaining_usd)
```

Counters live under a `tenant:<id>:cost` prefix (never colliding with request
counters) and are stored as **integer micro-dollars**, so the store's atomic
integer increments (`INCRBY`) stay exact — no float drift across workers. No
counters are written for tenants with no cost limits configured, so the
feature adds zero store traffic until a budget exists.

The engine itself lives in `core/quotas/cost_budgets.py` (`CostBudgetMixin`,
mixed into `QuotaManager`), with the shared calendar-window helpers
(`QuotaWindow`, period ids, TTLs) in `core/quotas/windows.py` — both split out
of `manager.py` for the 500-line cap. `core.quotas.manager` remains the
compatibility import path: `CostBudgetExceededError`, `CostWindowStatus`,
`TenantCostStatus`, and `QuotaWindow` are re-exported there (and from the
`core.quotas` package root), so existing imports keep working.

### Automatic enforcement in the LLM service

`core/quotas/cost_enforcement.py` is the ambient seam wired into
[`LLMService`](services.md#cost-control) — both the generation and the
streaming path:

- `enforce_tenant_cost_budget()` gates each call **before** any provider
  spend (at stream start for streaming); `record_tenant_llm_cost(usd)` books
  the call's real USD cost afterwards (at stream end for streaming).
- The tenant is resolved from the ambient context
  (`core.context.get_tenant_or_default()`), same as tenant request quotas —
  no plumbing through call sites. When an ambient user id is bound
  (`core.context.get_current_user_id()`), the same gate and booking also
  apply to that identity's [budget](#identity-usd-cost-budgets): tenant
  **and** identity budgets both enforce.
- Both helpers **fail open on infrastructure errors**: a quota-store outage
  degrades to unmetered service with a warning, never to an outage of its
  own. The budget rejection itself (`CostBudgetExceededError`) always
  propagates, and the LLM span records
  `gen_ai.baselith.error=tenant_cost_budget_exceeded`.

## Identity USD cost budgets

The same engine also caps a **single identity's** (API key / user) cumulative
LLM spend, independent of the tenant aggregate — one heavy user inside an
otherwise healthy tenant can be bounded on their own. Configure defaults via
`QUOTA_IDENTITY_DAILY_COST_USD` / `QUOTA_IDENTITY_MONTHLY_COST_USD` (default
`None` = unlimited; requires `QUOTAS_ENABLED=true`), override per identity at
runtime, and use the mirrored manager API — identical post-paid semantics,
counters under an `identity:<id>:cost` prefix:

```python
from core.config.quotas import get_key_cost_overrides, set_key_cost_budget
from core.quotas import get_quota_manager

set_key_cost_budget("partner-key-id", daily=5.0, monthly=80.0)
get_key_cost_overrides("partner-key-id")  # (5.0, 80.0); (None, None) if unset

manager = get_quota_manager()
await manager.check_identity_cost_budget("partner-key-id")  # gate the next call
await manager.record_identity_cost("partner-key-id", 0.0042)  # book after it
status = await manager.peek_identity_cost("partner-key-id")
print(status.windows["daily"].remaining_usd)
```

Enforcement is automatic through the same
[LLM-service seam](#automatic-enforcement-in-the-llm-service): when an ambient
user id is bound, each call is gated against the identity budget *and* the
tenant budget, and its real cost is booked against both. Without a bound user
(background jobs, unauthenticated surfaces), only the tenant budget applies.

## Configuration

| Variable                  | Default  | Description                              |
| ------------------------- | -------- | ---------------------------------------- |
| `QUOTAS_ENABLED`               | `false`  | Master switch                            |
| `QUOTA_DAILY_REQUESTS`         | unlimited| Default daily request budget per identity |
| `QUOTA_MONTHLY_REQUESTS`       | unlimited| Default monthly request budget per identity |
| `QUOTA_TENANT_DAILY_REQUESTS`  | unlimited| Default daily request budget per tenant (aggregate) |
| `QUOTA_TENANT_MONTHLY_REQUESTS`| unlimited| Default monthly request budget per tenant (aggregate) |
| `QUOTA_TENANT_DAILY_COST_USD`  | unlimited| Default daily USD cost budget per tenant (aggregate LLM spend) |
| `QUOTA_TENANT_MONTHLY_COST_USD`| unlimited| Default monthly USD cost budget per tenant (aggregate LLM spend) |
| `QUOTA_IDENTITY_DAILY_COST_USD`  | unlimited| Default daily USD cost budget per identity (API key / user) |
| `QUOTA_IDENTITY_MONTHLY_COST_USD`| unlimited| Default monthly USD cost budget per identity (API key / user) |
| `QUOTA_BACKEND`                | `redis`  | `redis` (shared across workers) or `memory` |

## Storage

`QuotaStore` is a pluggable Protocol. `RedisQuotaStore` (`INCRBY` + `EXPIRE`)
shares counters across workers and bounds stale keys with a TTL anchored to the
window's first request; `InMemoryQuotaStore` is the single-process default and
the fallback when Redis is unavailable. The protocol also exposes batched
`get_many` (Redis `MGET`) and `incr_many` (pipeline) operations — implemented by
both stores — which power `check_and_consume_pair`.
