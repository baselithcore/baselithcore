# Database Layer

The `core/db/` module manages persistent feedback and interaction data and
owns the shared PostgreSQL connection pools (sync and async). It exposes a
**function-based API** built on `psycopg` 3 — there are no repository
classes here.

## Module Structure

```txt
core/db/
├── connection.py   # Sync + async connection / cursor helpers and pool management
├── documents.py    # Document feedback aggregation helpers
├── feedback.py     # Feedback persistence and analytics functions
├── schema.py       # Schema bootstrap via Alembic migrations
├── serializers.py  # Source/row (de)serialization helpers
└── ...
```

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
from core.db.documents import get_document_feedback_summary, build_document_stats

# Aggregated stats per document cited across feedback entries
summary = await get_document_feedback_summary(min_total=0)

# Pure helper: build stats from raw rows (returns (stats, aliases))
stats, aliases = build_document_stats(rows)
```

---

## Schema Management

Schema is managed through Alembic migrations. `ensure_schema()` runs
`alembic upgrade head`; `init_db()` wraps it and is a no-op when PostgreSQL
is disabled.

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
```

!!! info "Statement timeout"
    Both the sync and async connection pools set `statement_timeout = 30 000 ms` at the PostgreSQL session level. Any query running longer than 30 seconds is automatically cancelled by the server, preventing slow-query attacks and runaway analytics from starving the pool.

To override the limit for specific long-running operations (e.g. migrations), run the following inside that transaction:

```sql
SET LOCAL statement_timeout = 0;
```

!!! warning "Multi-Tenancy"
    Feedback functions resolve and persist `tenant_id` from the current
    tenant context to enforce data isolation. Never bypass this with raw SQL
    that omits the tenant column.
