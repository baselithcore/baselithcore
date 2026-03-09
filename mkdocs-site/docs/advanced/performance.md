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

### Plugin Lazy Loading

Plugins are **scanned** at startup (AST metadata only), but **imported** only upon first use:

```python
# At startup: fast scan of available plugins (~50ms for 20 plugins)
# Each plugin is loaded ONLY when it receives the first request

# This means:
# - Fast startup
# - Memory used only for actively used plugins
# - Rarely used plugins do not impact performance
```

**Configuration:**

```env
# Disable lazy loading (load everything at startup)
PLUGIN_LAZY_LOADING=false  # Default: true
```

### Service Lazy Loading

Heavy services (LLM clients, DB connections) are initialized on-demand:

```python
from core.di import LazyProxy

# The service is created ONLY on first access
heavy_service = LazyProxy(lambda: HeavyService())

# First call: initializes the service (might be slow)
# Subsequent calls: reuse the already created instance
result = await heavy_service.process(data)
```

**Benefits:**

- Fast application startup
- Resources allocated only when needed
- Easy circular dependency management

---

## Caching

Caching is the most effective tool for improving performance. The framework offers multiple strategies.

### Redis Cache - Basic Usage

```python
from core.cache import Cache

cache = Cache()

# Set with TTL (Time To Live) in seconds
await cache.set("user:123:profile", user_data, ttl=300)  # 5 minutes

# Get (returns None if does not exist or expired)
cached_data = await cache.get("user:123:profile")

if cached_data is None:
    # Cache miss: load from database
    cached_data = await db.get_user(123)
    await cache.set("user:123:profile", cached_data, ttl=300)
```

### Cache Decorator

For common cache-aside patterns, use the decorator:

```python
from core.cache import cached

@cached(ttl=60, key_prefix="api")
async def expensive_operation(param1: str, param2: int):
    """
    Function is called only if result is not in cache.
    Cache key is generated automatically from parameters.
    """
    result = await slow_external_api(param1, param2)
    return result

# First call: executes function, saves to cache
result1 = await expensive_operation("foo", 42)

# Second call (within 60s): returns from cache
result2 = await expensive_operation("foo", 42)  # Instant!
```

### LLM Response Caching

LLM responses are expensive (time and money). Enable caching for identical prompts:

```python
from core.services.llm import get_llm_service

llm = get_llm_service()

# Automatic caching for identical contexts
response = await llm.generate(
    prompt="Explain photosynthesis",
    cache=True,  # Enable caching
    cache_ttl=3600  # 1 hour
)

# Second call with same context: returns from cache
response2 = await llm.generate(
    prompt="Explain photosynthesis",
    cache=True
)  # Instant, no LLM call!
```

!!! tip "When to Cache LLM"
    - ✅ Knowledge base queries (stable answers)
    - ✅ Translations (same input → same output)
    - ❌ Conversational chat (context-dependent)
    - ❌ Creative generation (variability desired)

### Multi-Level Cache Strategy

To maximize hit rate:

```python
from core.cache import multilevel_cached

@multilevel_cached(
    l1_ttl=10,      # In-memory cache (10 seconds)
    l2_ttl=300,     # Redis cache (5 minutes)
    l3_ttl=3600     # Disk cache (1 hour) - for very large data
)
async def get_embedding(text: str):
    return await embedding_service.embed(text)
```

---

## Connection Pooling

Database connections are expensive resources. Pooling reuses them.

### PostgreSQL

```python
# Pool configuration in .env
DATABASE_POOL_SIZE=20       # Connections kept open
DATABASE_MAX_OVERFLOW=10    # Extra connections in case of peak
DATABASE_POOL_TIMEOUT=30    # Connection wait timeout
DATABASE_POOL_RECYCLE=1800  # Recycle connections after 30 min
```

**Tuning:**

- `POOL_SIZE`: ~2-4 connections per CPU core
- `MAX_OVERFLOW`: 50% of pool size
- Monitor "connection wait time" to see if larger pool is needed

### Redis

```python
REDIS_POOL_SIZE=10          # Redis connections in pool
REDIS_SOCKET_TIMEOUT=5      # Operation timeout
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
# Use different models for different tasks
llm_fast = get_llm_service(model="gpt-3.5-turbo")  # For classification
llm_smart = get_llm_service(model="gpt-4")         # For complex reasoning
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
async for chunk in llm.stream("Explain relativity"):
    yield chunk  # Send immediately to client
```

### 4. Limit Context Window

More context = more processing time:

```python
from core.memory import truncate_context

# Limit messages in memory
messages = await memory.get_messages(
    session_id=session_id,
    max_messages=20,  # Only last 20 messages
    max_tokens=4000   # Limit also by token count
)
```

---

## Profiling

When you need to understand where time goes, use the built-in profiler:

### Request Profiling

```python
from core.observability import profile

@profile(name="my_handler")
async def handle_request(request):
    # Profiler automatically tracks:
    # - Total time
    # - Time in each await
    # - Memory allocation
    result = await process(request)
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
# Example: limit in-memory cache size
from cachetools import LRUCache

# Max 1000 entries, oldest are evicted
local_cache = LRUCache(maxsize=1000)
```
