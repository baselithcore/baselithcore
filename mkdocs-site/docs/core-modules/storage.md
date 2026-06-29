# Storage Layer

The `core/storage/` module is the **interaction and feedback repository
layer**. It defines abstract repository interfaces, Pydantic models for
interactions and feedback, and a PostgreSQL-backed implementation. It is a
typed, domain-shaped persistence API — not a generic key-value store.

## Module Structure

```yaml
core/storage/
├── interfaces.py  # InteractionRepository + FeedbackRepository (ABCs)
├── models.py      # Interaction, Feedback (Pydantic models)
└── postgres.py    # PostgresStorage — implements both repositories
```

---

## Repository Interfaces

Two abstract base classes (`abc.ABC`) define the contract. All methods are
async.

```python
from core.storage.interfaces import InteractionRepository, FeedbackRepository
```

### `InteractionRepository`

| Method | Signature | Returns |
|--------|-----------|---------|
| `store_interaction` | `(interaction: Interaction)` | `Interaction` |
| `get_interaction` | `(interaction_id: UUID)` | `Optional[Interaction]` |
| `get_interactions_by_session` | `(session_id: str, limit=100, offset=0)` | `List[Interaction]` |

### `FeedbackRepository`

| Method | Signature | Returns |
|--------|-----------|---------|
| `store_feedback` | `(feedback: Feedback)` | `Feedback` |
| `get_feedback_for_interaction` | `(interaction_id: UUID)` | `List[Feedback]` |
| `get_feedback_summary` | `(agent_id: Optional[str] = None)` | `Dict[str, Any]` |

---

## Models

```python
from core.storage.models import Interaction, Feedback
```

### `Interaction`

| Field | Type | Default |
|-------|------|---------|
| `id` | `UUID` | auto (`uuid4`) |
| `session_id` | `Optional[str]` | `None` |
| `user_id` | `Optional[str]` | `None` |
| `agent_id` | `Optional[str]` | `None` |
| `input_transcription` | `Optional[str]` | `None` |
| `output_transcription` | `Optional[str]` | `None` |
| `metadata` | `Dict[str, Any]` | `{}` |
| `timestamp` | `datetime` | auto (UTC now) |

### `Feedback`

| Field | Type | Default |
|-------|------|---------|
| `id` | `UUID` | auto (`uuid4`) |
| `interaction_id` | `UUID` | — (required) |
| `score` | `Optional[float]` | `None` |
| `label` | `Optional[str]` | `None` |
| `comment` | `Optional[str]` | `None` |
| `metadata` | `Dict[str, Any]` | `{}` |
| `timestamp` | `datetime` | auto (UTC now) |

---

## PostgreSQL Implementation

`PostgresStorage` implements **both** `InteractionRepository` and
`FeedbackRepository`. It is constructed with a `StorageConfig` (no DSN
string argument) and must be `initialize()`-d before use.

!!! info "Tenant scoping"
    Both tables carry a `tenant_id` column (DEFAULT `'default'`, indexed) and
    every read/write is scoped to `get_current_tenant_id()`, so interactions and
    feedback are isolated per tenant. Outside a request context the store degrades
    to `"default"`. See [Multi-Tenancy](../advanced/multi-tenancy.md).

```python
from core.config import get_storage_config
from core.storage.postgres import PostgresStorage
from core.storage.models import Interaction, Feedback

storage = PostgresStorage(config=get_storage_config())
await storage.initialize()        # opens the pool, ensures schema

# Store an interaction
interaction = await storage.store_interaction(
    Interaction(
        session_id="sess-123",
        user_id="user-42",
        agent_id="researcher",
        input_transcription="What is RAG?",
        output_transcription="RAG stands for...",
    )
)

# Attach feedback to that interaction
await storage.store_feedback(
    Feedback(
        interaction_id=interaction.id,
        score=1.0,
        label="positive",
        comment="Very helpful!",
    )
)

# Read back
again = await storage.get_interaction(interaction.id)
session_history = await storage.get_interactions_by_session("sess-123", limit=50)
fb = await storage.get_feedback_for_interaction(interaction.id)
summary = await storage.get_feedback_summary(agent_id="researcher")

# Liveness probe
ok = await storage.health_check()
```

The underlying connection pool is managed via `psycopg` — all operations are
non-blocking.

---

## Configuration

`StorageConfig` (`core/config/storage.py`) reads the standard `DB_*`
environment variables (there is no `POSTGRES_DSN`):

```bash
DB_HOST=postgres
DB_PORT=5432
DB_NAME=baselith
DB_USER=baselith
DB_PASSWORD=your-strong-password   # SecretStr — required in production
DB_SSL_MODE=                       # optional libpq sslmode
# Alternatively, provide a full URL:
DATABASE_URL=postgresql://user:password@host:5432/baselith
```

!!! note "vs core/db"
    `core/storage/` is the **typed interaction/feedback repository layer**
    (Pydantic models + ABCs). `core/db/` is the **lower-level, function-based
    document/feedback store** (chunks, schema, analytics). Both target the
    same PostgreSQL instance via the shared connection pool in
    `core/db/connection.py`.
