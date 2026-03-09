# Storage Layer

The `core/storage/` module provides an async PostgreSQL-backed storage abstraction, with typed interfaces and Pydantic models for document, feedback, and session persistence.

## Module Structure

```yaml
core/storage/
├── interfaces.py  # StorageInterface — abstract protocol
├── models.py      # Pydantic storage models
└── postgres.py    # AsyncPostgresStorage — production implementation
```

---

## Storage Interface

All storage backends implement this protocol:

```python
from core.storage.interfaces import StorageInterface

async def save(key: str, value: dict) -> None: ...
async def load(key: str) -> Optional[dict]: ...
async def delete(key: str) -> None: ...
async def list_keys(prefix: str = "") -> list[str]: ...
async def exists(key: str) -> bool: ...
```

---

## PostgreSQL Storage

```python
from core.storage.postgres import AsyncPostgresStorage

storage = AsyncPostgresStorage(dsn="postgresql://user:pass@localhost/db")

await storage.save("session:user-123", {"last_query": "...", "history": [...]})

session = await storage.load("session:user-123")
print(session["last_query"])

await storage.delete("session:user-123")
```

The underlying connection pool is managed via `psycopg[pool]` — all operations are non-blocking.

---

## Storage Models

```python
from core.storage.models import StorageRecord

record = StorageRecord(
    key="session:user-123",
    value={"history": []},
    ttl=3600,          # Optional TTL in seconds
    tenant_id="t-abc", # Optional tenant scoping
)
```

---

## Configuration

```bash
POSTGRES_DSN=postgresql://user:password@localhost:5432/baselith
```

!!! note "vs core/db"
    `core/storage/` is a **high-level key-value abstraction** — use it for session and plugin state. `core/db/` is the **low-level document store** with full schema (chunks, feedback, metadata). Use `core/db/` for document lifecycle management.
