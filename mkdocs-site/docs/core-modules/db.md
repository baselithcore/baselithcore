# Database Layer

<!-- markdownlint-disable MD046 -->

The `core/db/` module manages persistent feedback and interaction data and
owns the shared PostgreSQL connection pools (sync and async). It exposes a
**function-based API** built on `psycopg` 3 — there are no repository
classes here.

## Module Structure

```txt
core/db/
├── connection.py     # Sync + async connection / cursor helpers and pool management
├── session_setup.py  # Per-checkout session setup: timezone + RLS tenant binding
├── documents.py       # Document feedback aggregation helpers
├── feedback.py        # Feedback persistence and analytics functions
├── schema.py          # Schema bootstrap via Alembic migrations
├── serializers.py     # Source/row (de)serialization helpers
└── ...
```

`session_setup.py` holds what used to live directly in `connection.py` — the
timezone-apply helpers and, now, `system_tenant_scope()` — split out purely to
keep `connection.py` under the file-size cap. `connection.py` re-exports every
name (`APP_TIMEZONE_NAME`, `SYSTEM_TENANT_ID`, `system_tenant_scope`, plus the
private `_sync_apply_timezone`/`_async_apply_timezone`), so existing imports
and test monkeypatches against `core.db.connection.<name>` are unaffected.

---

## Connection Pool

Connections and cursors are obtained through context-manager helpers. There
is no public `get_pool()` — the pools (`_get_pool` / `_get_async_pool`) are
internal and opened lazily on first use.

```python
from core.db.connection import (
    get_connection,       # sync connection (context manager)
    get_cursor,           # sync cursor (context manager)
    get_async_connection, # async connection (async context manager)
    get_async_cursor,     # async cursor (async context manager)
    close_pool,           # close the sync pool
    close_async_pool,     # close the async pool
)

# Async usage
async with get_async_cursor() as cur:
    await cur.execute("SELECT 1")
    row = await cur.fetchone()

# Sync usage
with get_cursor() as cur:
    cur.execute("SELECT 1")
    row = cur.fetchone()

# Clean shutdown (e.g. in a worker teardown hook)
close_pool()
await close_async_pool()
```

Both `get_cursor` and `get_async_cursor` accept an optional keyword-only
`row_factory` (e.g. `psycopg.rows.dict_row`).

### Pool observability

`get_pool_stats()` returns a read-only snapshot of psycopg_pool's own counters
(`pool_size`, `pool_available`, `requests_waiting`, …) for every pool that has
actually been **created** — it never builds or opens a pool, so calling it from
a health endpoint can't trigger a connection. Keys are the pool roles:
`primary`, `primary_async`, `replica`, `replica_async`. Stats are best-effort
telemetry: a pool whose counters cannot be read is skipped and logged at
`debug` as `pool_stats_unavailable` with its role, never raised.

### Row-Level Security session binding

Opt-in via `DB_RLS_ENABLED` (default `false`, byte-identical connection path
when off). When enabled, every pool checkout — primary and read-replica, sync
and async — binds `app.tenant_id` to the current request's tenant with
`SELECT set_config('app.tenant_id', …, false)`, memoized on the connection so
an unchanged tenant costs no extra round-trip on repeat checkouts from the
same physical connection. This is the session half of row-level security; see
[Multi-Tenancy › Row-Level Security](../advanced/multi-tenancy.md#defense-in-depth-row-level-security)
for the policies and the role setup that make it actually isolate rows.

Outside a request (a background task, a script) the tenant contextvar may be
unbound, and what that means now depends on the flag:

| `DB_RLS_ENABLED` | Unbound caller |
| --- | --- |
| `false` (default) | Binds `"default"`, exactly as before |
| `true` | Raises `core.context.TenantContextError` — no tenant is bound, so `app.tenant_id` cannot be set |

The check asks `core.context.tenant_is_bound()`, not what
`get_current_tenant_id()` resolves to, so it fails closed regardless of
`strict_tenant_isolation`. Work that legitimately runs outside a request wraps
itself in `system_tenant_scope()` instead of being guessed at:

```python
from core.db.connection import system_tenant_scope

with system_tenant_scope():
    await run_maintenance()
```

`system_tenant_scope()` binds the tenant context to `SYSTEM_TENANT_ID`
(`"system"`) for the whole block and restores the previous value on exit, even
if the body raises — a plain `contextmanager`, so it works in both sync and
async code. Because it binds the tenant *context* (not just the DB session),
everything else that scopes by tenant sees the same explicit identity too.

### Who creates the schema

`core.db.ddl` holds the schema-ownership policy: **Alembic owns every table**.
A store that self-initializes its schema on the shared pool forces the runtime
role to hold DDL privileges in production and leaves the table with no migration
history. `skip_runtime_ddl()` gates the four stores that used to do it
(`core.a2a.task_store_postgres`, `core.orchestration.checkpoint_postgres`,
`core.prompts.store_postgres`, `core.storage.postgres`):

| `DB_RUNTIME_DDL` | Behaviour |
|---|---|
| unset (default) | allowed outside production, refused when `APP_ENV=production` |
| `true` | always allowed — single-role local Postgres |
| `false` | never allowed — the migrations Job owns the schema |

When refused, the store logs `runtime_ddl_skipped` at `debug` and continues; the
table is expected to exist already. A genuinely missing table then surfaces as an
ordinary query error naming it, instead of an opaque permission error on a
`CREATE`.

`RLS_PROTECTED_TABLES` lists the tenant-scoped tables carrying a
`tenant_isolation` row-level-security policy. See
[Multi-Tenancy](../advanced/multi-tenancy.md#defense-in-depth-row-level-security)
for the two-role deployment that makes those policies effective.

### Is row-level security actually enforced?

Enabling `DB_RLS_ENABLED` and running the migrations puts three things in place
— the pool binds `app.tenant_id`, the tables carry a policy, the policy reads
the GUC — and **none of them means isolation is enforced**. PostgreSQL skips a
policy entirely for:

- a **`SUPERUSER`** role — policies never apply to one;
- a role carrying **`BYPASSRLS`**;
- the **table owner**, unless the table also has `FORCE ROW LEVEL SECURITY`,
  which migration 008 deliberately does not set so that migrations and
  un-tenanted maintenance keep working.

The default `DB_USER` is the role that owns everything Alembic creates, so a
deployment can have RLS switched on in three places and no isolation at all,
with nothing in the logs saying so.

`core/db/rls_posture.py` closes that gap. At startup, when `DB_RLS_ENABLED` is
on, it reads the three vectors back from the catalogs and reports what it finds:

```python
from core.db.rls_posture import describe_rls_bypass, probe_rls_posture

posture = await probe_rls_posture(conn)
problem = describe_rls_bypass(posture)   # None when isolation really applies
```

In **production** a bypass **refuses the boot** — a deployment that believes the
database is isolating tenants and is wrong has no other moment to find out.
Outside production, or with `BASELITH_ALLOW_RLS_BYPASS=true` (an auditable
escape hatch, like `BASELITH_ALLOW_UNVALIDATED_HOST`), it logs at ERROR instead.
With `DB_RLS_ENABLED` off the check is a no-op: nothing was claimed, so there is
nothing to contradict.

The remedy it points at is a least-privilege role, provisioned for you by
`database.runtimeRole` in the Helm chart or by
`deploy/postgres/initdb/10-runtime-role.sh` under `compose.rls.yaml`:

```sql
CREATE ROLE baselith_runtime LOGIN PASSWORD '…'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
```

Migrations keep running as the owner; only the serving processes drop to it.

### Who runs the migrations

`init_db()` runs the Alembic upgrade at boot unless
`DB_MIGRATIONS_ON_STARTUP=false` (the pre-deploy migration-job mode the Helm
chart uses). The upgrade itself runs inside `system_tenant_scope()`: schema
work happens at boot, outside any request, so there is no tenant to inherit —
with `DB_RLS_ENABLED=true` the [session binding](#row-level-security-session-binding)
above refuses to invent one, so migrations have to name themselves as system
work.

The app's startup path initializes Postgres eagerly only when a **plugin**
declares it a required resource. Core's own tables — `chat_feedback`,
`interactions`, `feedback`, `tenants` — are not a plugin's concern, so
`should_run_core_schema_init()` covers the gap: when Postgres is enabled and no
plugin requires it, the lifespan still runs the upgrade through
`init_core_schema_best_effort()`.

That path is deliberately non-fatal. "Postgres enabled but no plugin needs it,
and the database is down" was a degraded boot before; making the fallback fatal
would turn it into a crash loop. The failure is logged at `error` as
`core_schema_init_failed`, which is what an operator needs when the first write
starts returning 500s.

!!! warning "Before this existed"
    A deployment whose enabled plugins listed Postgres as merely *optional*
    booted healthy with no schema at all, and the first `POST /v1/feedback`
    returned 500 with `relation "chat_feedback" does not exist`.

```python
from core.db import get_pool_stats

for role, counters in get_pool_stats().items():
    print(role, counters.get("pool_available"), counters.get("requests_waiting"))
```

### Read replicas (opt-in)

Set `DB_REPLICA_URL` to route **read-only** queries to a Postgres read replica
and offload the primary. Use the dedicated read API:

```python
from core.db import get_read_connection, get_async_read_connection

async with get_async_read_connection() as conn:
    await conn.execute("SELECT ...")   # served by the replica when configured
```

Behaviour is **additive and safe**:

- When `DB_REPLICA_URL` is unset, the read API transparently falls back to the
  primary pool — existing call sites are unchanged.
- The replica pool is created lazily only when configured.
- Use it only for queries that tolerate replication lag; never for writes or
  read-after-write within the same logical operation (those must use the primary
  `get_connection` / `get_async_connection`).

`close_pool()` / `close_async_pool()` also close the replica pools.

---

## Feedback Persistence

`core/db/feedback.py` exposes async module functions. Tenant scoping is
applied automatically from the current tenant context.

```python
from core.db.feedback import (
    insert_feedback,
    get_feedbacks,
    get_feedback_analytics,
)

# Insert a feedback row (feedback is "positive" or "negative")
await insert_feedback(
    query="What is RAG?",
    answer="RAG stands for...",
    feedback="positive",
    conversation_id="conv-123",
    sources=[{"doc_id": "doc-1", "score": 0.95}],
    comment="Very helpful!",
)

# List feedback, optionally filtered and limited
positives = await get_feedbacks("positive", limit=50)

# Rich analytics: counts, daily time series, recent + top queries, cited sources
analytics = await get_feedback_analytics(days=30, recent_limit=20, top_limit=10)
```

!!! note "Bounded scans"
    `get_feedback_analytics()` always applies a time window — when `days` is
    `None` it falls back to `feedback_analytics_default_days` (default 90) — and
    the per-document source aggregation is capped at
    `feedback_analytics_doc_scan_limit` rows (default 10 000). This keeps the
    cited-sources rollup from degrading into an unbounded full-table scan as the
    feedback table grows.

---

## Document Feedback Aggregation

`core/db/documents.py` provides helpers that aggregate feedback per cited
document — not a document CRUD repository.

```python
from core.db.documents import (
    fetch_document_feedback_rows,
    get_document_feedback_summary,
    build_document_stats,
)

# Aggregated stats per document cited across feedback entries
summary = await get_document_feedback_summary(min_total=0)

# The retried read behind it, if you want the raw rows
rows = await fetch_document_feedback_rows(tenant_id, since)

# Pure helper: build stats from raw rows (returns (stats, aliases))
stats, aliases = build_document_stats(rows)
```

Keys in `summary` are the canonical document key (`id::…`) plus every alias
(`path::…`, `url::…`) pointing at the same aggregate, so a retrieval hit can be
looked up by whichever identifier it carries.

### Where the rows come from

The rollup reads **`chat_feedback`**, not `feedback`. The two tables are not
interchangeable: `feedback` scores interactions and carries no citations, while
the `feedback` / `sources` / `timestamp` columns this aggregation needs live on
`chat_feedback` — the same source `core.db.feedback.get_feedback_analytics`
uses for its document rollup.

!!! warning "Read-only result"
    `get_document_feedback_summary()` hands back the **cached** mapping, shared
    by every concurrent caller. Never mutate it (or the per-document dicts
    inside it) — copy first if you need to annotate the aggregate. Aliases point
    at the *same* dict object as their canonical key, so a write through one key
    is visible through all of them and through every other request served from
    that cache entry.

### Caching on the RAG hot path

`RetrievalScoringMixin.apply_feedback` (`core/chat/mixins/retrieval_scoring.py`,
mixed into `RetrievalPipeline`) calls this rollup each time the feedback step
runs — `score_documents` schedules it (`next_action = "apply_feedback"`) while
`FEEDBACK_BOOST_ENABLED` is `true` (the default) — and each call scans up to
`FEEDBACK_ANALYTICS_DOC_SCAN_LIMIT`
rows and aggregates them in Python. Recomputing that per request is pure
overhead, so the result is memoised:

- **Per `(tenant_id, min_total)` TTL cache** — a `TTLCache` (maxsize 256,
  metrics name `document_feedback_summary`) holding the aggregate for
  `FEEDBACK_SUMMARY_CACHE_TTL` seconds (**default `60.0`**; `0` disables the
  cache and recomputes on every call).
- **Single-flight** — concurrent misses for the same key are coalesced, so a
  cold cache under load triggers one scan rather than one per in-flight
  request. The winner re-checks the cache after acquiring the slot.

### Retry policy

`fetch_document_feedback_rows()` is wrapped in `@retry(max_attempts=3,
base_delay=0.5, exponential_base=2.0)` restricted to
`RETRYABLE_DB_ERRORS = (psycopg.OperationalError, psycopg.InterfaceError)`.

!!! info "Retry only what can succeed on a second try"
    Transient connection faults are worth re-running; a schema or SQL error is
    deterministic. Retrying the latter only multiplies latency and pool
    checkouts before failing the request anyway — three attempts with
    exponential backoff on a query that can never succeed is a self-inflicted
    stall on the request path. Scope retry predicates to the faults that are
    actually transient.

---

## Schema Management

Schema is managed through Alembic migrations. `ensure_schema()` runs
`alembic upgrade head`; `init_db()` wraps it and is a no-op when PostgreSQL
is disabled. `init_db()` additionally runs that call inside
[`system_tenant_scope()`](#who-runs-the-migrations) — `ensure_schema()` called
directly does not, so a caller invoking it outside `init_db()` under
`DB_RLS_ENABLED=true` must bind its own tenant context first.

```python
from core.db.schema import init_db, ensure_schema

# Idempotent: applies pending Alembic migrations (skips if POSTGRES_ENABLED is false)
await init_db()

# Or run migrations directly
await ensure_schema()
```

Migrations under `migrations/versions/` create the core tables, including
`tenants`, `chat_feedback`, and `interactions`.

---

## Configuration

```bash
POSTGRES_ENABLED=true
DB_HOST=localhost
DB_PORT=5432
DB_NAME=baselith
DB_USER=baselith
DB_PASSWORD=your-strong-password   # SecretStr — required when APP_ENV=production
DB_POOL_MIN_SIZE=1                 # Minimum connections in pool
DB_POOL_MAX_SIZE=20                # Maximum connections in pool
DB_POOL_TIMEOUT=30.0               # Seconds to wait for an available connection
DB_STATEMENT_TIMEOUT_MS=30000      # Server-side cap per statement (0 = unbounded)
DB_RLS_ENABLED=false               # Bind app.tenant_id per checkout for row-level security — see below
DB_IDLE_IN_TRANSACTION_TIMEOUT_MS=60000  # Kill a session idle inside an open transaction
```

Feedback aggregation (read by `core/db/documents.py` at import time):

| Variable | Default | Effect |
| -------- | ------- | ------ |
| `FEEDBACK_ANALYTICS_DEFAULT_DAYS` | `90` | Lookback window for the rollup scan |
| `FEEDBACK_ANALYTICS_DOC_SCAN_LIMIT` | `10000` | Hard row cap on that scan |
| `FEEDBACK_SUMMARY_CACHE_TTL` | `60.0` | Seconds the document rollup is cached per `(tenant, min_total)`; `0` disables caching |

!!! info "Session budgets"
    Every pool (sync, async, and both read-replica pools) bakes two server-side
    guards into the libpq startup options of each connection, so no
    per-checkout `SET` round-trip is needed:

    - `statement_timeout` (`DB_STATEMENT_TIMEOUT_MS`, default 30 000 ms) —
      any statement running longer is cancelled by the server, preventing
      slow-query attacks and runaway analytics from starving the pool.
    - `idle_in_transaction_session_timeout` (`DB_IDLE_IN_TRANSACTION_TIMEOUT_MS`,
      default 60 000 ms) — a session that opened a transaction and went quiet
      (leaked connection, handler crashed mid-transaction) is terminated
      before the locks it holds block everyone else. The pools run in
      autocommit mode, so only explicit transactions are affected.

    Set either to `0` to hand the decision back to the server defaults.

To override the limit for specific long-running operations (e.g. migrations), run the following inside that transaction:

```sql
SET LOCAL statement_timeout = 0;
```

!!! warning "Multi-Tenancy"
    Feedback functions resolve and persist `tenant_id` from the current
    tenant context to enforce data isolation. Never bypass this with raw SQL
    that omits the tenant column.
