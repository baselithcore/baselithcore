# Caching System

The `core/cache/` module provides a **tiered, pluggable caching system** with four implementations: in-memory TTL, Redis, Semantic (vector-similarity), and a local file cache.

## Module Structure

```txt
core/cache/
├── protocols.py       # CacheProtocol family — typed Protocol interfaces
├── local_cache.py     # TTLCache — in-memory async TTL + LRU cache
├── ttl_cache.py       # Backward-compat re-export of TTLCache
├── redis_cache.py     # RedisTTLCache — Redis-backed async cache
└── semantic_cache.py  # SemanticLLMCache — vector-similarity LLM cache
```

---

## TTL Cache (In-Memory)

Best for: single-process deployments, ephemeral data, rate limiting.

```python
from core.cache import TTLCache  # defined in core.cache.local_cache

cache = TTLCache(maxsize=1000, ttl=300)  # 5-minute TTL, LRU eviction

await cache.set("key", {"data": "value"})
value = await cache.get("key")   # None if expired
await cache.delete("key")
await cache.clear()

# Batch operations
await cache.set_many([("a", 1), ("b", 2)])
values = await cache.get_many(["a", "b"])
```

`maxsize` and `ttl` both default to values from the cache config when
omitted; both must be positive. The TTL is fixed at construction —
`set()` takes only `(key, value)`. The interface is fully async.

---

## Redis Cache (FalkorDB Compatible)

Best for: multi-process deployments, session data, shared state. BaselithCore uses the same FalkorDB instance for caching.

`RedisTTLCache` wraps an existing async `Redis` client — it does not take a
URL. Build the client with `create_redis_client()` (or your own), then pass
it in:

```python
from core.cache import RedisTTLCache
from core.cache.redis_cache import create_redis_client

client = create_redis_client()  # reads REDIS_URL / cache config
cache: RedisTTLCache = RedisTTLCache(
    client,
    prefix="baselith",       # keyword-only; defaults to the configured cache_prefix
    default_ttl=300,         # keyword-only; seconds
)

await cache.set("key", {"data": "value"})
value = await cache.get("key")
```

Configure via `.env`:

```bash
REDIS_URL=redis://localhost:6379
```

---

## Semantic Cache

Best for: LLM response caching — avoids redundant inference for semantically identical queries.

```python
from core.cache import SemanticLLMCache

cache = SemanticLLMCache(
    maxsize=1000,
    ttl=3600,
    threshold=0.92,   # cosine similarity threshold
)

# Store a prompt/response pair
await cache.set(prompt="What is RAG?", response="RAG stands for...")

# Retrieve if a semantically similar prompt exists (returns the response str or None)
hit = await cache.get_similar("Explain RAG to me")
if hit:
    print(hit)        # Cached response string

# Or get the response together with the similarity score
response, score = await cache.get_similar_with_score("Explain RAG to me")
print(response, score)  # ("RAG stands for...", 0.96)
```

The semantic cache uses the same embedding model as the VectorStore, ensuring consistency. It features **asynchronous embedding generation** to prevent blocking the event loop and implements a **multi-tenant LRU (Least Recently Used) eviction policy** based on both access time and frequency (hits).

!!! tip "Multi-Tenant Isolation"
    All LLM caching mechanisms (both exact-match `TTLCache` and `SemanticLLMCache`) automatically namespace their keys with the current `tenant_id` to prevent cross-tenant data leakage.

---

## Single-Flight (stampede protection)

`core/cache/single_flight.py` coalesces concurrent cache-miss fills:

- **`SingleFlight`** — in-process: only the first caller for a key runs the
  factory; concurrent callers share the result (or exception). Wired into
  `LLMService.generate_response` miss handling.
- **`RedisSingleFlight`** — **cross-worker**: elects one owner per key via a
  Redis `SET NX EX` lock; other workers poll with exponential backoff,
  re-reading the caller's cache via the `recheck` callable until the owner
  finishes or the lock TTL elapses. Release is **token-guarded** (Lua
  compare-and-delete) so a worker can never delete a lock another worker
  re-acquired after a TTL expiry. **Fail-open by design**: on Redis errors or
  timeout the waiter computes the value itself — an occasional duplicate
  upstream call, never a deadlocked request.

```python
from core.cache.single_flight import RedisSingleFlight

sf = RedisSingleFlight(ttl_seconds=30)
value = await sf.do(cache_key, factory, recheck=lambda: cache.get(cache_key))
```

---

## Cache Protocol

Caches conform to the `CacheProtocol` family in `core/cache/protocols.py`.
The base `CacheProtocol` defines the core async surface; `TTLCacheProtocol`
adds a `ttl` argument to `set`, and `BatchCacheProtocol` adds bulk ops:

```python
from core.cache import CacheProtocol

# CacheProtocol (base):
async def get(key) -> Optional[V]: ...
async def set(key, value) -> None: ...
async def delete(key) -> None: ...
async def clear() -> None: ...

# BatchCacheProtocol adds:
async def get_many(keys) -> list[Optional[V]]: ...
async def set_many(items) -> None: ...
```

There is no `exists` method on the protocol. This typing lets you swap
implementations without changing business logic:

```python
# Swap from in-memory to Redis behind the same protocol
cache: CacheProtocol = (
    RedisTTLCache(create_redis_client())
    if settings.REDIS_URL
    else TTLCache(ttl=300)
)
```

!!! tip "Tiered Caching"
    The framework uses a two-tier pattern internally: **Semantic Cache** (first hit) → **Redis** (persistent) → **LLM inference** (last resort). This dramatically reduces token costs.
