# Caching System

The `core/cache/` module provides a **tiered, pluggable caching system** with four implementations: in-memory TTL, Redis, Semantic (vector-similarity), and a local file cache.

## Module Structure

```txt
core/cache/
├── protocols.py       # CacheProtocol — abstract interface
├── ttl_cache.py       # In-memory TTL cache (thread-safe)
├── redis_cache.py     # Redis-backed async cache
├── semantic_cache.py  # Vector-similarity cache for LLM responses
└── local_cache.py     # File-system-backed cache
```

---

## TTL Cache (In-Memory)

Best for: single-process deployments, ephemeral data, rate limiting.

```python
from core.cache.ttl_cache import TTLCache

cache = TTLCache(default_ttl=300)  # 5 minutes TTL

await cache.set("key", {"data": "value"}, ttl=60)  # Override TTL
value = await cache.get("key")   # None if expired
await cache.delete("key")
await cache.clear()
```

---

## Redis Cache

Best for: multi-process deployments, session data, shared state.

```python
from core.cache.redis_cache import RedisCache

cache = RedisCache(url="redis://localhost:6379", prefix="baselith:")

await cache.set("session:user-123", session_data, ttl=3600)
data = await cache.get("session:user-123")
```

Configure via `.env`:

```bash
REDIS_URL=redis://localhost:6379
```

---

## Semantic Cache

Best for: LLM response caching — avoids redundant inference for semantically identical queries.

```python
from core.cache.semantic_cache import SemanticCache

cache = SemanticCache(
    similarity_threshold=0.92,  # Cosine similarity threshold
    max_size=1000,
    ttl=3600,
)

# Store LLM response
await cache.store(query="What is RAG?", response="RAG stands for...")

# Retrieve if semantically similar query exists
hit = await cache.lookup("Explain RAG to me")
if hit:
    print(hit.response)   # Cached response
    print(hit.similarity) # 0.96
```

The semantic cache uses the same embedding model as the VectorStore, ensuring consistency.

!!! tip "Multi-Tenant Isolation"
    All LLM caching mechanisms (both exact-match `TTLCache` and `SemanticCache`) automatically namespace their keys with the current `tenant_id` to prevent cross-tenant data leakage.

---

## Cache Protocol

All caches implement the same `CacheProtocol` interface:

```python
from core.cache.protocols import CacheProtocol

async def get(key: str) -> Optional[Any]: ...
async def set(key: str, value: Any, ttl: Optional[int] = None) -> None: ...
async def delete(key: str) -> None: ...
async def clear() -> None: ...
async def exists(key: str) -> bool: ...
```

This allows you to swap implementations without changing business logic:

```python
# Swap from in-memory to Redis with no code changes
cache: CacheProtocol = (
    RedisCache(url=settings.REDIS_URL)
    if settings.REDIS_URL
    else TTLCache(default_ttl=300)
)
```

!!! tip "Tiered Caching"
    The framework uses a two-tier pattern internally: **Semantic Cache** (first hit) → **Redis** (persistent) → **LLM inference** (last resort). This dramatically reduces token costs.
