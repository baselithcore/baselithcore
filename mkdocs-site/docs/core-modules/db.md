# Database Layer

The `core/db/` module manages all persistent document data: document chunks, feedback signals, schemas, and serialization. Built on async PostgreSQL via `psycopg3`.

## Module Structure

```txt
core/db/
├── connection.py   # Async connection pool management
├── documents.py    # Document CRUD (chunks, metadata, embeddings)
├── feedback.py     # Feedback persistence (thumbs up/down, ratings)
├── schema.py       # Schema creation and migrations
├── serializers.py  # Pydantic ↔ DB row serializers
└── utils.py        # Query helpers, pagination
```

---

## Connection Pool

```python
from core.db.connection import get_pool, close_pool

# Initialize at startup (called by bootstrap)
pool = await get_pool(dsn="postgresql://...")

# Clean shutdown
await close_pool()
```

The pool is a singleton — inject it via `Depends(get_pool)` in FastAPI routes.

---

## Document Operations

```python
from core.db.documents import DocumentRepository

repo = DocumentRepository(pool=pool)

# Insert a document chunk
chunk_id = await repo.insert_chunk(
    document_id="doc-123",
    chunk_index=0,
    content="This is the first chunk.",
    embedding=[0.1, 0.2, ...],  # 384-dim vector
    metadata={"page": 1, "source": "report.pdf"},
    tenant_id="t-abc",
)

# Retrieve chunks for a document
chunks = await repo.get_chunks(document_id="doc-123", tenant_id="t-abc")

# Delete all chunks for a document
await repo.delete_document(document_id="doc-123", tenant_id="t-abc")

# List all documents for a tenant
docs = await repo.list_documents(tenant_id="t-abc", limit=50, offset=0)
```

---

## Feedback Persistence

```python
from core.db.feedback import FeedbackRepository

repo = FeedbackRepository(pool=pool)

await repo.insert(
    query="What is RAG?",
    answer="RAG stands for...",
    feedback="positive",   # or "negative"
    conversation_id="conv-123",
    comment="Very helpful!",
    sources=[{"doc_id": "doc-1", "score": 0.95}],
)

# Analytics
stats = await repo.get_stats(tenant_id="t-abc")
# {"positive": 142, "negative": 8, "ratio": 0.95}
```

---

## Schema Management

```python
from core.db.schema import initialize_schema

# Creates all tables if they don't exist (idempotent)
await initialize_schema(pool)
```

Tables created:

- `documents` — document metadata
- `chunks` — text chunks with vector embeddings
- `feedback` — user feedback signals
- `interactions` — full conversation log (JSONB)

---

## Configuration

```bash
POSTGRES_DSN=postgresql://user:password@localhost:5432/baselith
DB_POOL_MIN=2     # Minimum connections in pool
DB_POOL_MAX=10    # Maximum connections in pool
```

!!! warning "Multi-Tenancy"
    All queries **must** include `tenant_id` to ensure data isolation. The repository layer enforces this at the query level — never bypass it with raw SQL.
