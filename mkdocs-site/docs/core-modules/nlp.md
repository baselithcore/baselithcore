# NLP Utilities

The `core/nlp/` module provides Natural Language Processing utilities built on **spaCy**, with graceful degradation when the spaCy library or models are unavailable.

## Module Structure

```yaml
core/nlp/
├── spacy_utils.py   # Lazy-loaded spaCy pipeline with fallback
└── models.py        # Embedding model loader (sentence-transformers)
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
embedder = get_embedder("sentence-transformers/all-MiniLM-L6-v2")
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

### Embedding cache & miss coalescing

`CachedEmbedder` fronts the sentence-transformers model with a TTL cache keyed
by the sha256 of the input text: a `RedisTTLCache` when `CACHE_BACKEND=redis`
(key prefix `<CACHE_REDIS_PREFIX>:embed:<dim>`), else an in-process `TTLCache`.
If the Redis client fails to build, the embedder logs a warning and runs
uncached rather than failing.

Concurrent misses for the *same single text* (the stampede-prone shape: many
requests embedding the same query) are coalesced through a
`LayeredSingleFlight` built via `build_single_flight`, keyed by the same
sha256 the cache uses, so a popular query is encoded once instead of once per
concurrent caller. Batch encodes are untouched to preserve model-level
batching.

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

| Setting | Default | Notes |
| ------- | ------- | ----- |
| `BASELITH_INFERENCE_THREADS` | `min(4, cpu_count // 2)` | Small on purpose: torch and sentence-transformers parallelise internally, so extra threads buy contention rather than throughput. Raise it only with a measurement to justify it. |

The pool is built on first use and shut down at interpreter exit.
