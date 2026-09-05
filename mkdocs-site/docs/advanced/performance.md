---
title: Performance
description: Optimization and caching
---

Performance is critical in BaselithCore where every request can involve multiple LLM calls, vector searches, and database accesses. This guide helps you identify bottlenecks and optimize the system.

!!! warning "Premature Optimization"
    **Do not optimize prematurely**. Measure first, then optimize. Premature optimization is the root of all evil (D. Knuth). Use observability tools to identify real bottlenecks.

---

## When to Optimize

Optimize when:

| Symptom         | Typical Threshold | Action                                   |
| --------------- | ----------------- | ---------------------------------------- |
| Request latency | > 3 seconds       | Profile and identify bottleneck          |
| High CPU usage  | > 80% sustained   | Scale horizontally or optimize code      |
| High memory     | > 85%             | Look for memory leaks, reduce cache size |
| Cache miss rate | > 40%             | Review caching strategy                  |
| Error rate      | > 1%              | Prioritize debugging over performance    |

**Before optimizing**, ensure you have:

1. Baseline metrics (see [Observability](observability.md))
2. Clear bottleneck identification
3. Measurable goal (e.g., "reduce p95 from 3s to 1s")

---

## Lazy Loading

The framework implements lazy loading to minimize startup time and memory usage.

### Plugin Loading

Plugins are **discovered** at startup in lazy-import mode (manifest metadata
only, no module import). What happens next depends on `PLUGIN_AUTO_LOAD`
(`core/api/lifespan.py`):

- `PLUGIN_AUTO_LOAD=true` (the default): every plugin marked `enabled: true` in
  `configs/plugins.yaml` is **activated during startup**, so its routers and
  handlers are mounted before the first request lands. Plugins left disabled in
  the config stay discovered-but-unloaded and cost nothing until an admin
  enables them at runtime through the admin plugin API
  (`POST /api/plugins/{plugin_name}/enable`, `core/plugins/api.py`).
- `PLUGIN_AUTO_LOAD=false`: startup stops at discovery. Every plugin endpoint is
  404 until the plugin is enabled through that same admin call — faster boot,
  but the activation cost moves to the first operator action instead of
  disappearing.

**Configuration:**

```env
# Automatically discover/load plugins on startup (PluginConfig.auto_load)
PLUGIN_AUTO_LOAD=true   # Default: true

# Disable the plugin system entirely (PluginConfig.enabled)
PLUGIN_ENABLED=true     # Default: true
```

### Service Lazy Loading

Heavy services (LLM clients, DB connections) are initialized on-demand via the
`LazyServiceRegistry` (`core/di/lazy_registry.py`). See
[Lazy Loading](lazy-loading.md) for the full reference.

```python
from core.di import get_lazy_registry

registry = get_lazy_registry()

# Factories are registered under string keys — "postgres", "vectorstore", "llm",
# "graph", "redis", "memory", "hierarchical_memory", "evaluation", "evolution"
# (RESOURCE_FACTORIES in core/bootstrap/lazy_init.py). The API lifespan registers
# the ones the installed plugins declare; get_or_create() raises KeyError for a
# key without a factory. The resource is created ONLY on first access, then memoized.
llm = await registry.get_or_create("llm")
```

**Benefits:**

- Fast application startup
- Resources allocated only when needed
- Easy circular dependency management

---

## Caching

Caching is the most effective tool for improving performance. The framework offers multiple strategies.

### In-process Cache (`TTLCache`)

`TTLCache` (`core/cache/local_cache.py`) is an async-safe, in-memory LRU cache
with a per-instance TTL:

```python
from core.cache import TTLCache

# ttl is in seconds and maxsize bounds memory; both fall back to CacheConfig
# (ttl_default=300.0, maxsize_default=256) when omitted. metrics_name labels
# this instance in the cache metrics collector (default "ttl_cache").
cache: TTLCache[str, dict] = TTLCache(maxsize=1000, ttl=300, metrics_name="user_profiles")

# Set / get (get returns None on miss or expiry)
await cache.set("user:123:profile", user_data)
cached_data = await cache.get("user:123:profile")

if cached_data is None:
    # Cache miss: load from database
    cached_data = await db.get_user(123)
    await cache.set("user:123:profile", cached_data)
```

### Redis Cache (`RedisTTLCache`)

`RedisTTLCache` (`core/cache/redis_cache.py`) shares the same `get`/`set`
interface, backed by Redis. The TTL and key prefix are configured on the
instance, not per call:

```python
from core.cache import RedisTTLCache, create_redis_client
from core.config.cache import get_redis_cache_config

# The URL is required; pools are shared per (url, decode_responses) pair
client = create_redis_client(get_redis_cache_config().url)  # CACHE_REDIS_URL
cache: RedisTTLCache[str, dict] = RedisTTLCache(
    client,
    prefix="api",
    default_ttl=60,  # seconds
)

await cache.set("foo:42", result)
result = await cache.get("foo:42")  # None on miss
```

!!! note "No decorator helpers"
    There are no `Cache`, `@cached`, or `@multilevel_cached` helpers. Use the
    `TTLCache` / `RedisTTLCache` classes directly (both implement
    `get`/`set`/`get_many`/`set_many`/`delete`/`clear`).

### LLM Response Caching (`SemanticLLMCache`)

LLM responses are expensive (time and money). `SemanticLLMCache`
(`core/cache/semantic_cache.py`) caches responses by semantic similarity of the
prompt and is **tenant-partitioned** (keyed by `get_current_tenant_id()`):

```python
from core.cache import SemanticLLMCache

llm_cache = SemanticLLMCache()

# Look up a cached response for a semantically-similar prompt
cached = await llm_cache.get("Explain photosynthesis")
if cached is None:
    cached = await llm.generate_response(prompt="Explain photosynthesis")
    await llm_cache.set("Explain photosynthesis", cached)
```

!!! tip "When to Cache LLM"
    - ✅ Knowledge base queries (stable answers)
    - ✅ Translations (same input → same output)
    - ❌ Conversational chat (context-dependent)
    - ❌ Creative generation (variability desired)

---

## Connection Pooling

Database connections are expensive resources. Pooling reuses them.

### PostgreSQL

```env
# Pool configuration in .env (see core/config/storage.py)
DB_POOL_MIN_SIZE=1     # Minimum pooled connections (psycopg_pool)
DB_POOL_MAX_SIZE=20    # Maximum pooled connections
DB_POOL_TIMEOUT=30     # Seconds to wait for a free connection
```

**Tuning:**

- `DB_POOL_MAX_SIZE`: ~2-4 connections per CPU core
- Keep `DB_POOL_MIN_SIZE` low so idle workers do not hold connections
- Monitor connection wait time to see if a larger pool is needed

### Redis

Redis connection pooling is configured through the connection URLs rather than
discrete pool-size env vars:

```env
CACHE_REDIS_URL=redis://localhost:6379/1   # General cache (RedisTTLCache)
QUEUE_REDIS_URL=redis://localhost:6379/2   # Task queue
GRAPH_DB_URL=redis://localhost:6379        # Graph memory backend
```

---

## LLM Optimization

LLM calls are often the biggest bottleneck. Strategies to reduce latency and costs:

### 1. Choose the Right Model

| Task                  | Recommended Model                 | Typical Latency |
| --------------------- | --------------------------------- | --------------- |
| Intent classification | Small model (GPT-3.5, Mistral 7B) | 200-500ms       |
| Complex generation    | Large model (GPT-4, Claude 3)     | 2-10s           |
| Embedding             | text-embedding-3-small            | 50-100ms        |

```python
from core.services.llm import get_llm_service

llm = get_llm_service()

# get_llm_service() returns the shared service; choose the model per call
fast = await llm.generate_response(prompt=text, model="gpt-3.5-turbo")   # classification
smart = await llm.generate_response(prompt=text, model="gpt-4")          # complex reasoning
```

### 2. Batch Requests

When possible, group requests:

```python
# ❌ Slow: 10 sequential calls
for doc in documents:
    embedding = await embed(doc)

# ✅ Fast: 1 batch call
embeddings = await embed_batch(documents)  # 5-10x faster
```

### 3. Streaming for UX

Use streaming for long responses. User sees first tokens immediately:

```python
from core.services.llm import get_llm_service

llm = get_llm_service()

# generate_response_stream(prompt, model=None, system_prompt=None, ...)
async for chunk in llm.generate_response_stream("Explain relativity"):
    yield chunk  # Send immediately to client
```

### 4. Limit Context Window

More context = more processing time:

```python
from core.memory import AgentMemory

memory = AgentMemory()

# Build a token-bounded context string from working memory.
# Context folding (if configured) compresses older items automatically.
context = await memory.get_context_async(max_tokens=4000)
```

---

## Profiling

When you need to understand where time goes, instrument spans with the tracer or
drop down to `cProfile`.

### Span-based timing

There is no built-in `@profile` decorator. Wrap hot paths in tracer spans (see
[Observability](observability.md)) to time them within a distributed trace:

```python
from core.observability import get_tracer

tracer = get_tracer("my-handler")

async def handle_request(request):
    with tracer.start_span("handle_request") as span:
        result = await process(request)
        span.set_attribute("result_count", len(result))
        return result
```

### Manual Profiling

```python
import cProfile
import pstats

# Profile a code section
profiler = cProfile.Profile()
profiler.enable()

await my_slow_function()

profiler.disable()
stats = pstats.Stats(profiler)
stats.sort_stats('cumulative')
stats.print_stats(20)  # Top 20 functions by time
```

### Memory Profiling

```python
from memory_profiler import profile

@profile
def memory_intensive_function():
    large_list = [i for i in range(1000000)]
    return process(large_list)
```

---

## Typical Benchmarks

Reference performance for standard installation (4 CPU, 16GB RAM):

| Operation                | Typical Latency | Notes             |
| ------------------------ | --------------- | ----------------- |
| Health check             | < 5ms           | Network only      |
| Cache hit                | 1-2ms           | Local Redis       |
| Cache miss + DB          | 10-50ms         | Local PostgreSQL  |
| Embedding (short text)   | 50-100ms        | OpenAI API        |
| LLM completion (GPT-3.5) | 200-800ms       | Depends on tokens |
| LLM completion (GPT-4)   | 2-10s           | Depends on tokens |
| Vector search (10k docs) | 20-50ms         | Qdrant            |
| Vector search (1M docs)  | 100-300ms       | Qdrant with index |

---

## Performance Checklist

Before going to production:

- [ ] LLM Cache enabled for repeated queries
- [ ] Connection pooling configured
- [ ] Lazy loading active for plugins
- [ ] Prometheus metrics configured
- [ ] Grafana Monitoring Dashboard
- [ ] Alert for p95 latency > threshold
- [ ] Load test executed with expected load

---

## Best Practices

!!! tip "Measure First"
    Always use profiling and metrics before optimizing. Don't guess where problems are.

!!! tip "Smart Caching"
    Cache expensive results (LLM, embedding), not everything. Over-caching can cause stale data.

!!! tip "Async Everything"
    All I/O operations must be `async`. A single `time.sleep()` blocks the entire event loop.

!!! warning "Memory Limits"
    Set memory limits for caches and buffers. Without limits, the entire system can crash.

```python
# Example: bound an in-process cache
from core.cache import TTLCache

# Max 1000 entries (LRU eviction), each expiring after 300 seconds
local_cache: TTLCache[str, object] = TTLCache(maxsize=1000, ttl=300)
```

---

## Microbenchmarks

Throughput of the small, hot, in-process primitives the framework runs on nearly
every request — no network, DB, or LLM calls, so the harness is deterministic and
reproducible:

```bash
python benchmarks/run.py            # human-readable
python benchmarks/run.py --markdown # Markdown (table below)
```

Representative single-machine run (Python 3.12):

| Operation | Throughput (ops/sec) | Latency (µs/op) |
| --------- | -------------------: | --------------: |
| prompt render (`{{var}}`) | 1,085,844 | 0.921 |
| scope match (wildcard) | 5,513,820 | 0.181 |
| webhook HMAC sign | 681,716 | 1.467 |
| cursor paginate (1k seq) | 832,747 | 1.201 |
| per-tenant key derive (HKDF) | 105,422 | 9.486 |
| field encrypt+decrypt (AES-GCM) | 293,325 | 3.409 |
| orjson dumps | 2,978,784 | 0.336 |
| stdlib json.dumps | 274,173 | 3.647 |

Every request-path primitive is **sub-10µs**; most are well under 1µs. The
`orjson`-vs-stdlib row (≈10× faster) is why response serialization uses `orjson`
throughout. Numbers are **indicative and machine-specific** — run the harness on
your own hardware before quoting; use them for relative comparison and
regression spotting. See `benchmarks/README.md`.
