---
title: Unified Storage (FalkorDB)
description: Three-tier architecture for resource optimization via FalkorDB
---

The system uses a **Unified Storage** architecture powered by **FalkorDB** (a Redis-compatible engine) to optimize resource usage and ensure logical isolation between different critical functionalities.

---

## The Problem

Instead of requiring multiple Redis instances, the system leverages **Database IDs** (0-15 standard) to segregate data based on persistence, volatility, and operational purpose.

---

## Tier Architecture (FalkorDB)

```mermaid
graph LR
    App[Application] --> T0[Tier 0: Structural]
    App --> T1[Tier 1: Ephemeral]
    App --> T2[Tier 2: Operational]

    T0 --> DB0[(Redis DB 0<br/>FalkorDB Graph)]
    T1 --> DB1[(Redis DB 1<br/>Cache/PubSub)]
    T2 --> DB2[(Redis DB 2<br/>RQ Queue)]
```

| Tier | Database ID | Logical Name | Main Purpose | Persistence |
|------|-------------|--------------|--------------|-------------|
| **Tier 0** | `0` | **Structural** | Knowledge Graph (**FalkorDB**) | High (Stateful) |
| **Tier 1** | `1` | **Ephemeral** | Caching, PubSub (Redis-compatible) | Low (Cache) |
| **Tier 2** | `2` | **Operational** | Task Queue (RQ) (Redis-compatible) | Medium (Transient) |

---

## Tier 0: Structural Storage (DB 0)

Used by **FalkorDB** to store the system's Knowledge Graph. BaselithCore requires FalkorDB (a Redis fork) for Tier 0 to support high-performance graph operations.

### Tier 0 Content

- Entities, relationships, ontologies loaded by plugins
- Long-term structured memory

### Tier 0 Configuration

```env
GRAPH_DB_URL=redis://localhost:6379
GRAPH_DB_NAME=agent_graph
GRAPH_DB_TIMEOUT=2
```

These are the `StorageConfig` defaults (mirrored in `.env.example`). A URL
without a `/N` suffix selects Redis database `0`; `GRAPH_DB_ENABLED` gates the
tier.

---

## Tier 1: Ephemeral Storage (DB 1)

Used for all high-speed operations that do not require long-term persistence.

### Tier 1 Content

- **RedisTTLCache** / **TTLCache**: Cache for LLM results and heavy computations
- **SemanticLLMCache**: Semantic (vector) cache for similar prompts
- **Rate Limiting**: Counters for traffic control
- **PubSub**: Real-time communication between components

### Tier 1 Configuration

```env
CACHE_REDIS_URL=redis://localhost:6379/1
```

### Mechanics

Data in this tier can be deleted (`FLUSHDB`) without impacting the structural stability of the system.

```bash
# Flush only the cache
redis-cli -n 1 FLUSHDB
```

---

## Tier 2: Operational Storage (DB 2)

Dedicated to asynchronous process management and work queues.

### Tier 2 Content

- **RQ (Redis Queue)**: Job definitions and queues (`default`, `documents`, `analysis` — `TaskQueueConfig.queues`)
- **TaskTracker**: Task execution status (pending, running, failed, completed)

### Tier 2 Configuration

```env
QUEUE_REDIS_URL=redis://localhost:6379/2
```

`TaskQueueConfig` reads `QUEUE_REDIS_URL` (the `StorageConfig` default above) and
lets the more specific `TASK_QUEUE_REDIS_URL` override it.

### Note

Ensures workers can coordinate without interfering with the Knowledge Graph or application cache.

---

## Multi-Tenancy and Isolation

Beyond separation via Database ID, the system implements granular isolation via **Key Prefixing**:

### Key Structure

The tenant-aware `RedisCache` (`core/optimization/caching.py`) builds every key
as `{CACHE_REDIS_PREFIX}:{tenant_id}:{namespace}:{key}`, where `namespace` is
the per-cache prefix (`embedding`, `search`, `learner`, …):

```text
baselithcore:tenant-123:embedding:<sha256-of-text>
baselithcore:tenant-456:search:<query-hash>
```

### Rules

1. **Two prefixes, two settings.** `CACHE_REDIS_PREFIX`
   (`StorageConfig.cache_redis_prefix`, default `baselithcore`) namespaces the
   tenant-aware `RedisCache`, the graph query cache (`<prefix>:graph`) and the
   NLP model cache. `REDIS_CACHE_PREFIX` (`RedisCacheConfig.cache_prefix`,
   default `baselithcore:cache`) namespaces `RedisTTLCache`
   (`core/cache/redis_cache.py`) and the rate-limiter, idempotency, JWT-blacklist
   and API-key-denylist keys derived from it. Change both if you share one
   Redis between deployments.
2. **Tenant Isolation**: `RedisCache` and `SemanticLLMCache` partition entries by
   `get_current_tenant_id()`; `RedisTTLCache` keys are not tenant-scoped.
3. **Namespace Separation**: Never mix data of different nature in the same database.

---

## Benefits of Tiering

### Resource Efficiency

A single Redis instance can manage the entire infrastructure stack.

### Fault Isolation

A crash or memory saturation in Tier 1 (Cache) does not prevent queues (Tier 2) from functioning.

### Maintainability

You can flush cache or reset queues independently without touching Knowledge Graph data.

```bash
# Flush ONLY cache (Tier 1)
redis-cli -n 1 FLUSHDB

# Flush ONLY queues (Tier 2)
redis-cli -n 2 FLUSHDB

# Graph remains intact in DB 0
```

### Monitoring

Allows monitoring load and memory usage differentially for each system function.

```bash
# Monitor specific DB
redis-cli -n 1 INFO memory
redis-cli -n 2 INFO keyspace
```

---

## Production Configuration

### FalkorDB Configuration

```conf title="redis.conf"
# Enable RDB + AOF for Tier 0 (Graph)
save 900 1
save 300 10
save 60 10000
appendonly yes
appendfsync everysec

# Multi-DB support (default 16)
databases 16

# Memory eviction per DB
maxmemory-policy allkeys-lru
```

### Application Configuration

```python title="core/config/storage.py"
from pydantic import Field
from pydantic_settings import BaseSettings

class StorageConfig(BaseSettings):
    # Tier 0: Graph
    graph_db_url: str = Field(default="redis://localhost:6379", alias="GRAPH_DB_URL")
    graph_db_name: str = Field(default="agent_graph", alias="GRAPH_DB_NAME")
    graph_db_timeout: float = Field(default=2.0, alias="GRAPH_DB_TIMEOUT", ge=0.1)
    graph_cache_ttl: int = Field(default=3600, alias="GRAPH_CACHE_TTL")

    # Tier 1: Cache
    cache_redis_url: str = Field(
        default="redis://localhost:6379/1", alias="CACHE_REDIS_URL"
    )
    cache_redis_prefix: str = Field(default="baselithcore", alias="CACHE_REDIS_PREFIX")

    # Tier 2: Queue
    queue_redis_url: str = Field(
        default="redis://localhost:6379/2", alias="QUEUE_REDIS_URL"
    )
```

---

## Best Practices

!!! tip "Logical Separation"
    Never use the same DB for structural data and volatile cache.

!!! tip "Selective Backups"
    Configure automatic backups ONLY for Tier 0 (Graph), not for cache/queue.

!!! tip "Granular Monitoring"
    Monitor memory usage per database, not just globally.

!!! warning "Eviction Policy"
    Configure appropriate `maxmemory-policy` for each tier:
    - Tier 0: `noeviction` (Graph must persist)
    - Tier 1: `allkeys-lru` (Cache can be evicted)
    - Tier 2: `noeviction` (Job queue is critical)
