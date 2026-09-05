# Caching System

The `core/cache/` module provides a **tiered, pluggable caching system** with four implementations: in-memory TTL, Redis, Semantic (vector-similarity), and a local file cache.

## Module Structure

```txt
core/cache/
├── protocols.py       # CacheProtocol family — typed Protocol interfaces
├── local_cache.py     # TTLCache — in-memory async TTL + LRU cache
├── ttl_cache.py       # Backward-compat re-export of TTLCache
├── redis_cache.py     # RedisTTLCache — Redis-backed async cache + async pools
├── redis_sync.py      # Shared bounded pools for the *synchronous* client
├── semantic_cache.py  # SemanticLLMCache — vector-similarity LLM cache
├── single_flight.py   # SingleFlight / RedisSingleFlight — miss coalescing
└── metrics.py         # CacheMetricsCollector — hit/miss/eviction analytics
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
from core.config.cache import get_redis_cache_config

# url is required (keyword-only decode_responses=False); clients share one
# bounded pool per (url, decode_responses)
client = create_redis_client(get_redis_cache_config().url)
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
CACHE_REDIS_URL=redis://localhost:6379/1
```

`RedisTTLCache`, `create_redis_client` and the shared pools read
`RedisCacheConfig` (`core/config/cache.py`, `get_redis_cache_config()`):
`url` ← `CACHE_REDIS_URL` (default `redis://redis:6379/1`), `cache_prefix`
(default `baselithcore:cache`), `cache_ttl` (default `3600.0` s) and the pool
bounds (`max_connections` default `50`). `StorageConfig.cache_redis_url`
(`core/config/storage.py`) reads the **same** `CACHE_REDIS_URL` variable with a
different fallback (`redis://localhost:6379/1`) and is what
`core/bootstrap/lazy_init.py` and `core/chat/dependencies.py` use for the
bootstrap client — set the variable and both agree.

### XFetch probabilistic early refresh

`RedisTTLCache` ships with **XFetch** stampede protection (Vattani et al.,
"Optimal Probabilistic Cache Stampede Prevention") enabled by default. As an
entry nears expiry, one caller probabilistically treats a live hit as a miss and
recomputes **before** the TTL lapses, so the herd never hits a synchronized cold
key on rollover (which would otherwise hammer the embedder or LLM).

The behaviour is tuned by `early_refresh_beta` — the constructor kwarg, or the
`BASELITH_CACHE_XFETCH_BETA` env var when the kwarg is unset:

| Value | Effect |
| ----- | ------ |
| `1.0` | Canonical setting and the **default** — the protection is on |
| `0` | Disables early refresh entirely |
| `>1.0` | Recompute earlier (more aggressive) |
| `<1.0` | Recompute later (closer to expiry) |

```bash
# Turn XFetch off (default is 1.0)
BASELITH_CACHE_XFETCH_BETA=0
```

Independently, each written entry's TTL is jittered by up to +10% so entries
created in the same burst do not all lapse at once.

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

### The embedder contract

`_compute_embedding()` accepts **either** flavour of embedder and dispatches on
the callable, not on convention:

| Embedder | How it is called |
| -------- | ---------------- |
| `async def encode(...)` — e.g. the production `core.nlp.CachedEmbedder` | Awaited directly |
| `def encode(...)` — e.g. a raw `SentenceTransformer` | Offloaded via `core.utils.concurrency.run_inference` |

Both paths are invoked as `encode(text, convert_to_numpy=True)`, and the result
is L2-normalized once at insert time so the similarity scan is a single
matrix-vector product. A custom `embedder=` must therefore accept that keyword
and return something `np.asarray` can consume.

!!! warning "Offload to the inference pool, not the default executor"
    A synchronous embedder goes to the **dedicated inference pool**
    (`run_inference`), never `run_in_executor(None, …)`. The default executor
    serves latency-critical short tasks; parking a multi-tens-of-milliseconds
    sentence-transformer forward pass there starves them. Follow the same rule
    in any plugin that wraps a local model.

!!! danger "A swallowed embedding error is a silently dead cache"
    `set()` and `get_similar_with_score()` both wrap the embedding step in a
    broad `except` that logs at **warning** level and degrades to a no-op /
    miss. That is the right failure mode for a cache — but it means a
    misbehaving embedder produces a cache with a permanent 0% hit rate and no
    error anywhere. Watch `cache.stats["hit_rate"]` and the
    `Failed to compute embedding` warnings after swapping the embedder; a
    hit rate pinned at `0.0%` on repeated traffic means the embedding path is
    raising, not that the traffic is diverse.

---

## Single-Flight (stampede protection)

`core/cache/single_flight.py` coalesces concurrent cache-miss fills:

- **`SingleFlight`** — in-process: only the first caller for a key runs the
  factory; concurrent callers share the result (or exception). Wired into
  `LLMService.generate_response` miss handling, `SemanticLLMCache.get_or_compute`
  and `CachedEmbedder.encode`.
- **`RedisSingleFlight`** — **cross-worker**: elects one owner per key via a
  Redis `SET NX EX` lock; other workers poll with exponential backoff,
  re-reading the caller's cache via the `recheck` callable until the owner
  finishes or the lock TTL elapses. Release is **token-guarded** (Lua
  compare-and-delete) so a worker can never delete a lock another worker
  re-acquired after a TTL expiry. **Fail-open by design**: on Redis errors or
  timeout the waiter computes the value itself — an occasional duplicate
  upstream call, never a deadlocked request. When built from a URL it borrows
  the cache's shared bounded pool (`create_redis_client`, socket deadlines and
  health checks included) rather than opening a private unbounded client.
- **`LayeredSingleFlight`** — the composition of the two, and the class you
  should normally use.

## Connection pools

Every Redis client in `core/` comes from one of two shared registries, never
from a bare `Redis.from_url()`:

| Client | Factory | Registry keyed by |
| ------ | ------- | ----------------- |
| `redis.asyncio` | `create_redis_client()` | `(url, decode_responses)` |
| `redis` (sync) | `create_sync_redis_client()` | `(url, decode_responses, socket_timeout)` |

Both apply the same limits from the cache config: `max_connections`,
`health_check_interval`, `socket_timeout` and `socket_connect_timeout`. The
deadlines are the important half. redis-py sets **no** socket timeout by
default, so a Redis that accepts the connection and then stops answering
mid-command blocks the caller indefinitely while holding a pooled connection —
enough of those and the bounded pool is exhausted by callers that will never
return. The sync sites (graph, the A2A nonce ledger, the AP2 replay guard, the
synchronous rate limiter, the Redis scratchpad) each used to build their own
unbounded, deadline-free client.

`decode_responses` and the socket deadline are connection-level settings in
redis-py, which is why they are part of the key: two callers that disagree must
not share a pool, or one silently gets the other's settings. Pass
`socket_timeout` only when the component has its own budget — the graph client
does.

Closing a client never tears down the shared pool. redis-py only disconnects a
pool the client itself created (`auto_close_connection_pool`), so a component
closing its own handle cannot take the pool away from everyone else. Both
registries are drained on lifespan shutdown (`close_redis_pools()` and
`close_sync_redis_pools()`) so a rolling deploy releases server-side
connections promptly.

The two live in separate modules on purpose: `redis_cache` binds `Redis` and
`ConnectionPool` to the *asyncio* classes, and holding both meanings of those
names in one namespace invites returning a coroutine where a value was
expected.

---

### Why a distributed lock is not, by itself, coalescing

A lock gives **mutual exclusion**, not coalescing. The worker that *loses* the
lock still needs somewhere to get the value from; otherwise it waits and then
recomputes (no saving at all) or fails. The complete path is:

1. The **in-process layer** collapses this worker's N concurrent coroutines to
   one. Skipping it just moves the stampede off the backend and onto Redis:
   N coroutines racing for the lock, N-1 entering the polling path.
2. The **cross-worker layer** elects one worker out of W. The winner computes
   and **publishes** the value into the shared cache; the losers poll
   `recheck` — a read of that same shared cache — and return the winner's value.
   If the wait times out, a loser recomputes rather than failing.

The second layer is therefore only meaningful when the backing cache is
**genuinely shared**. Against a process-local store (a plain dict, `cachetools`)
the loser's `recheck` can never observe the winner's write, so it would pay the
polling latency and recompute anyway — strictly worse than plain in-process
coalescing. This is why `SemanticCache` and `LLMService`, whose caches are
process-local, deliberately stay on the in-process layer, while
`CachedEmbedder` opts in only when its resolved cache is a `RedisTTLCache`.

### Activation

Two independent conditions must hold, and **neither is the presence of a Redis
URL** — `CACHE_REDIS_URL` ships with a non-empty default while `CACHE_BACKEND`
defaults to `local`, so testing the URL alone would point a stock config at a
Redis that is not running:

| Condition | Why |
| --- | --- |
| `CACHE_CROSS_WORKER_SINGLE_FLIGHT=true` | Explicit opt-in — this adds a network round-trip to a hot cache-miss path. Default `false`. |
| The caller's cache is actually shared | What lets a losing worker read the winner's result. |

```python
from core.cache.single_flight import build_single_flight

# In-process only unless BOTH conditions hold.
sf = build_single_flight(shared_cache=isinstance(cache, RedisTTLCache))

value = await sf.do(
    cache_key,
    factory,                                  # computes AND publishes to cache
    recheck=lambda: cache.get(cache_key),     # how the loser gets the result
)
```

!!! warning "Fail-open, not fail-closed"
    If Redis is unreachable, coalescing degrades to the in-process behaviour and
    the request still succeeds. This is the opposite of the AP2 replay guard in
    `core/world_model/replay_guard.py`, which is deliberately fail-closed: there,
    a missing guard risks a double payment. Here the cost of a coordination
    failure is one extra LLM call, while the cost of failing closed would be an
    outage.

Deadlocks and orphan locks are bounded by construction: the lock always carries
a TTL (so a crashed owner is reaped), release is token-guarded compare-and-delete,
and the owner releases in a `finally` so a raising factory frees the lock
immediately instead of leaving the next caller to wait out the TTL.

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
from core.config import get_storage_config
from core.config.cache import get_redis_cache_config

cache: CacheProtocol = (
    RedisTTLCache(create_redis_client(get_redis_cache_config().url))
    if get_storage_config().cache_backend == "redis"  # CACHE_BACKEND
    else TTLCache(ttl=300)
)
```

!!! tip "Tiered Caching"
    The framework uses a two-tier pattern internally: **Semantic Cache** (first hit) → **Redis** (persistent) → **LLM inference** (last resort). This dramatically reduces token costs.
