# NLP Utilities

The `core/nlp/` module provides Natural Language Processing utilities built on **spaCy**, with graceful degradation when the spaCy library or models are unavailable.

## Module Structure

```yaml
core/nlp/
├── spacy_utils.py   # Lazy-loaded spaCy pipeline with fallback
├── models.py        # Embedding model loader (sentence-transformers)
└── lazy.py          # Async accessors + LazyEmbedder / LazyReranker
```

---

## spaCy Pipeline

The spaCy integration uses a **lazy, cached loader** — the model is only loaded on first use and cached for the lifetime of the process.

```python
from core.nlp.spacy_utils import get_spacy_pipeline, is_spacy_available, extract_spacy_metadata

# Check availability without triggering a load
if is_spacy_available():
    # Extract metadata from text
    metadata = extract_spacy_metadata("BaselithCore is a Python framework built in 2025.")
    print(metadata)
    # {
    #   "spacy_language": "en",
    #   "spacy_model": "en_core_web_sm",
    #   "spacy_token_count": "11",
    #   "spacy_sentence_count": "1",
    #   "spacy_entities": "BaselithCore (ORG); 2025 (DATE)"
    # }
```

### Fallback Behaviour

If the configured spaCy model is unavailable, the module falls back gracefully:

1. Tries to load the configured model (`spacy_model` in config)
2. If unavailable → creates a blank `Language` pipeline with `sentencizer`
3. If spaCy is not installed at all → returns `None` from `get_spacy_pipeline()`

No exceptions are raised in any case.

---

## Configuration

```bash
ENABLE_SPACY_DOCUMENTS=true       # Enable spaCy enrichment during document ingestion
SPACY_MODEL=en_core_web_sm        # spaCy model to load
SPACY_FALLBACK_LANGUAGE=en        # Language for blank fallback pipeline
```

Install spaCy and a model:

```bash
pip install spacy
python -m spacy download en_core_web_sm
```

---

## Embedding Models

```python
from core.nlp.models import get_embedder, get_reranker

# Load sentence-transformers embedder (cached per model name)
embedder = get_embedder("BAAI/bge-m3")
# CachedEmbedder.encode is a coroutine: the blocking model call is
# offloaded to the dedicated inference pool, so it must be awaited.
embeddings = await embedder.encode(["text one", "text two"])

# Load cross-encoder reranker (cached per model name); this is the plain
# sentence-transformers CrossEncoder, whose predict() is synchronous
reranker = get_reranker("cross-encoder/ms-marco-MiniLM-L-6-v2")
scores = reranker.predict([("query", "doc1"), ("query", "doc2")])
```

!!! tip "Performance"
    `get_embedder()` and `get_reranker()` are wrapped in `@functools.cache` (unbounded, one entry per `model_name`), so each model is loaded once and reused across all requests; `get_spacy_pipeline()` uses `@lru_cache(maxsize=1)` and holds a single pipeline.

!!! note "Importing `core.nlp` loads no ML stack"
    `sentence_transformers` (and with it transformers, torch, scipy and
    scikit-learn) is imported on the first `get_embedder()` / `get_reranker()`
    call, not when `core/nlp/models.py` is imported. The module sits on the
    application's import path (`core.api.lifespan` → `core.services.indexing`
    → `core.nlp`), and the former module-scope import was ~2.2 s and ~3 000
    modules of every process start: importing `core.api.factory` dropped from
    ~3.0 s / 4 618 modules to ~0.45 s / 1 373, and the same import inside the
    container image from ~2.9 s to ~0.6 s. `SentenceTransformer` and
    `CrossEncoder` stay reachable as module attributes (PEP 562), so
    `mock.patch("core.nlp.models.SentenceTransformer")` keeps working; with the
    `[rag]` extra absent they resolve to `None`.

### Loading models off the event loop

`get_embedder()` / `get_reranker()` build the model synchronously — seconds of
disk and CPU work on first call, and a `RuntimeError` when the `[rag]` extra is
not installed. From async code use the accessors in `core/nlp/lazy.py`, which
run the same cached factory in a worker thread (first loads are serialized, so
two concurrent callers never build the model twice):

```python
from core.nlp import aget_embedder, aget_reranker

embedder = await aget_embedder()          # VECTORSTORE_EMBEDDING_MODEL
reranker = await aget_reranker("cross-encoder/ms-marco-MiniLM-L-6-v2")
```

To hold a model you may never need — a dependency container built at boot —
store a stand-in instead: `LazyEmbedder(factory, model_name)` builds the model
on its first `await encode(...)` (off the loop), and
`LazyReranker(factory, model_name)` on its first `predict(...)`, which the
rerank path already runs on the inference pool. `loaded` reports whether the
model exists yet. The chat dependencies, the semantic LLM cache and the
hierarchical-memory bootstrap all load this way. The vector-store rerank
service (`core.services.retrieval.reranker.Reranker`) does too: `rerank()`
awaits `aload_model()`, which builds its CrossEncoder in a worker thread under
a lock, so the first rerank neither stalls the loop nor builds the model twice.

### Embedding cache & miss coalescing

`CachedEmbedder` fronts the sentence-transformers model with a TTL cache keyed
by `sha256(f"{model_id}:{text}")` (module-level `_cache_key`): a `RedisTTLCache`
when `CACHE_BACKEND=redis` (key prefix `<CACHE_REDIS_PREFIX>:embed:<dim>`), else
an in-process `TTLCache`. If the Redis client fails to build, the embedder logs
a warning and runs uncached rather than failing.

The model id is in the key because the Redis prefix only carries the embedding
**dimension**. Keying on the text alone made two models of the same width share
every entry — 384 is the common case, the width of the default
`sentence-transformers/all-MiniLM-L6-v2` — so one model's vector could be
returned for a query the other embedded, which corrupts every similarity score
computed from it. `CachedEmbedder` resolves the id once in `__init__`
(`self._model_id`, from `_model_name()`: the model card's base model or name,
falling back to the class name) — so two models that expose neither can still
collide, and models that carry their card metadata are what to pass.

!!! warning "The two embedding caches must key the same way"
    `core/services/vectorstore/embedding_cache.py` has always composed
    `sha256(f"{model_id}:{text}")`, and this one now matches. They are separate
    code paths under separate Redis prefixes, so they never read each other's
    entries; what they share is the rule, and a deployment is only free of
    cross-model collisions while both sides follow it. Change one, change both.

!!! info "The key format changed — one recompute per cached text"
    Entries written by an older build can no longer be addressed, so they are
    orphaned: never read again, expiring on their own TTL, with each affected
    text encoded once more on first use after the upgrade.

Concurrent misses for the *same single text* (the stampede-prone shape: many
requests embedding the same query) are coalesced through a
`LayeredSingleFlight` built via `build_single_flight`, keyed by the same
`_cache_key` the cache uses — so the lock is scoped per model as well as per
text, and a popular query is encoded once instead of once per concurrent
caller. Batch encodes are untouched to preserve model-level batching.

The **cross-worker layer** (one encoder per key across all workers/pods, via a
Redis lock; losers read the winner's embedding back out of the shared cache)
activates only when **both** hold:

1. `CACHE_CROSS_WORKER_SINGLE_FLIGHT=true` — explicit opt-in;
2. the *resolved* cache is a `RedisTTLCache` — decided on the actual instance,
   not on `CACHE_BACKEND`: if the Redis client failed to build and the embedder
   fell back to a local `TTLCache`, a distributed lock over a process-local
   store would only add latency before recomputing anyway.

Fail-open in every path: Redis unreachable degrades to plain in-process
coalescing, never to an error. Full design in
[Cache — single-flight](cache.md#single-flight-stampede-protection).

### Where inference runs

Model calls are CPU-bound and blocking, so the async wrappers offload them —
but **not** to the interpreter's default executor. That pool is shared with
every other `to_thread` caller in the framework (SSRF DNS resolution on the
browser route guard, audit-log appends, tokenization), and those tasks are
short and latency-critical: a burst of embedding or rerank work would fill the
pool and leave them queued behind multi-second model calls.

`core.utils.concurrency.run_inference` therefore dispatches to a dedicated
`ThreadPoolExecutor`, used by the async `CachedEmbedder.encode` and by both
cross-encoder rerank paths (`core/chat/reranking.py`,
`core/memory/hierarchy_search.py`).

`run_inference` also carries the caller's `contextvars` across the thread hop —
the way `asyncio.to_thread` does and a bare `run_in_executor(pool, fn)` does not.
Without that copy the worker runs in an **empty** context: every span opened
inside an offloaded call becomes a new trace root instead of a child of the
caller's span, and every contextvar-carried value — the tenant above all — is
unbound, so tenant-scoped state read inside the call resolves to a shared
`default` bucket, or raises under `strict_tenant_isolation`.

The copy is taken on the calling thread and is one-directional: anything the
callable *binds* stays in the worker, so an offload can never rewrite the caller's
tenant, span or budget.

!!! warning "The isolation is shallow — offload computation, not bookkeeping"
    Rebinding a contextvar in the worker is isolated; **mutating an object it
    inherited is not**. The copy holds the same `LoopBudget`, DI `Scope`, `Colony`
    and span objects the caller holds, so a callable that charges a budget or
    registers an agent from the worker thread mutates shared state off the event
    loop — no lock, and none of the single-threaded ordering the rest of the
    framework assumes. No current call site does: every one is pure CPU over data
    it was handed. Keep it that way when adding the next offload.

| Setting | Default | Notes |
| ------- | ------- | ----- |
| `BASELITH_INFERENCE_THREADS` | `min(4, cpu_count // 2)` | Small on purpose: torch and sentence-transformers parallelise internally, so extra threads buy contention rather than throughput. Raise it only with a measurement to justify it. |

The pool is built on first use and shut down at interpreter exit.
