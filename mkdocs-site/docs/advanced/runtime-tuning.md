# Runtime Tuning & Operational Knobs

This page documents the operational environment variables and the
standards-alignment behaviours introduced by the modern best-practices hardening
pass. All knobs are opt-out where a safe default exists.

## Environment variables

| Variable | Default | Applies to | Effect |
|----------|---------|-----------|--------|
| `BASELITH_MIGRATION_LOCK_TIMEOUT` | `5s` | Alembic migrations | Postgres `lock_timeout` for the migration session. Fails a blocked DDL fast instead of piling up application connections during a rolling deploy. Accepts a Postgres interval (`5s`, `250ms`, `5000`). |
| `BASELITH_MIGRATION_STATEMENT_TIMEOUT` | `0` (disabled) | Alembic migrations | Postgres `statement_timeout` for the migration session. Left disabled so long `CREATE INDEX CONCURRENTLY` builds are not aborted; set it to cap slow migrations. |
| `BASELITH_LLM_PROMPT_CACHE` | `true` | Anthropic provider | Marks the system prompt (the stable instructions/tool/RAG/memory prefix) with an ephemeral `cache_control` breakpoint so Anthropic reuses it (~5 min TTL) instead of re-billing it every call. Set to `false` to disable. |
| `BASELITH_TOOL_OUTPUT_MAX_CHARS` | `8000` | Agent loop | Character budget for a single tool result / observation before it is head+tail truncated (see below). `0` disables truncation. |
| `BASELITH_IDEMPOTENCY_ENABLED` | `true` | API | Enable the `Idempotency-Key` replay middleware (see below). |
| `BASELITH_IDEMPOTENCY_TTL_SECONDS` | `86400` | API | How long a captured response is replayable for a given key. |
| `BASELITH_IDEMPOTENCY_MAX_BODY_BYTES` | `1048576` | API | Responses larger than this are streamed through and not cached. |
| `BASELITH_MEMORY_HYBRID_RECALL` | `true` | Memory | Fuse dense (cosine) recall with a BM25 keyword pass via RRF (see below). Set to `false` for the legacy pure-cosine path. |
| `BASELITH_MEMORY_TTL_ENFORCE` | `true` | Memory | Enforce `TierConfig.ttl_seconds`: expired MTM/LTM items are swept during consolidation/compression and via `purge_expired()`. Set to `false` for legacy capacity-only eviction. |
| `BASELITH_REACT_HISTORY_MAX_TOKENS` | `8000` | Agent loop | Token budget for ReAct loop history (`core/reasoning/history.py`): beyond it, the oldest thoughts/observations are deterministically collapsed to head-excerpts (newest turns stay intact) so long runs have bounded prompt cost and never overflow the context window. `0` disables compaction. |
| `BASELITH_SCRATCHPAD_TTL_SECONDS` | `86400` | Memory | Sliding TTL for `RedisScratchpadBackend` threads (refreshed on write). `0` disables expiry. |
| `BASELITH_MEMORY_DECAY_PRUNE` | `false` | Memory | Run relevance-decay pruning (`RelevanceCalculator`: exponential age decay × importance) during maintenance sweeps — items past `max_age_days` or under `pruning_threshold` are evicted from MTM/LTM. `prune_low_relevance()` stays callable directly either way. |
| `BASELITH_MEMORY_RERANK` | `false` | Memory | Cross-encoder re-ordering of recalled top-k (reuses the chat reranker). Off by default: heavy optional dependency + hot-path latency. Fail-open; order-only (never changes which k items return). |
| `BASELITH_SWARM_MAX_SUBTASKS` | `4` | Swarm | Hard cap on model-emitted sub-tasks per decomposition — bounds the dynamic agents and parallel executions a single LLM completion can spawn (the "2-4" in the prompt is advisory only). Min 1. |

## Prompt caching (Anthropic)

The system prompt is the stable prefix re-sent on every call. When it exceeds
the model's cache minimum (~1024 tokens Sonnet / 2048 Haiku), it is sent as a
`cache_control: ephemeral` content block. On a cache hit, Anthropic returns the
reused input under `cache_read_input_tokens`; the provider now sums
`input + output + cache_read + cache_creation` so usage accounting is not
under-counted. Typically a large input-cost and latency win on long prefixes.

## Tool-output truncation

`core.orchestration.truncate_tool_output` caps a tool result before it re-enters
the context window, keeping a **head and a tail** (the payload framing and the
trailing status/error) and replacing the middle with a `… [truncated N chars] …`
marker. The cut is deterministic so replayed trajectories stay stable. Wired into
the ReAct observation path; reusable for any handler that renders tool output.

## Hybrid memory recall

`HierarchicalMemory.recall()` now fuses two signals instead of dense-only
cosine: the existing per-tier cosine ranking **and** a BM25 keyword pass over the
in-memory corpus, merged with Reciprocal Rank Fusion. BM25 rescues exact-token
hits (error codes, identifiers, rare terms) that fall below the cosine
threshold, while RRF makes the merge scale-free so STM/MTM/LTM scores no longer
have to share a scale. Near-duplicate contents across tiers are collapsed. The
query is still embedded exactly once (BM25 needs no embeddings). Set
`BASELITH_MEMORY_HYBRID_RECALL=false` to restore the pure-cosine path.

## RFC 9457 error responses

Every API error is now emitted as
[RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) `application/problem+json`,
including `HTTPException` and request-validation failures (previously the API
served two shapes). The document carries `type` (a stable `urn:baselith:error:*`
classifier), `title`, `status`, `detail`, `instance`, plus the `code` and
`request_id` extension members; validation errors add an `errors` array.

**Back-compat:** `detail` is a first-class RFC 9457 field, so consumers reading
`response["detail"]` keep working. `HTTPException` response headers (e.g.
`WWW-Authenticate`, `Retry-After`) are preserved.

## Rate-limit response headers

A `429 Too Many Requests` now carries the IETF `RateLimit-Limit`,
`RateLimit-Remaining`, `RateLimit-Reset` and standard `Retry-After` headers
(the reset seconds come from the same atomic Redis round trip). Rate-limit keys
are **tenant-scoped** (`{tenant}:{role}:…`) so buckets never collide across
tenants and per-tenant policies can be layered on later.

## Idempotency-Key replay

Mutating requests (`POST`/`PUT`/`PATCH`/`DELETE`) that carry an
`Idempotency-Key` header have their response captured and stored in Redis; a
later request with the same key **replays** the stored response (with an
`Idempotency-Replayed: true` header) instead of re-executing the side effect —
so a client or proxy retry never double-charges, double-writes, or double-sends.

The middleware is **pure ASGI** and streaming-safe: a `text/event-stream`
response (or one larger than `BASELITH_IDEMPOTENCY_MAX_BODY_BYTES`) is forwarded
chunk by chunk and never cached. A concurrent duplicate still in flight gets
`409 Conflict`; `5xx` responses are never cached (a retry gets a fresh attempt).
It is **fail-open** — if Redis is unavailable the request proceeds normally.

## Cache TTL jitter & embedding single-flight

Two stampede protections on the Redis-backed caches (2026-07 performance pass):

- **TTL jitter** — every `RedisTTLCache` write (`set`/`set_many`) spreads its
  TTL by up to **+10%** so entries written in the same burst don't all expire
  at once (synchronized mass-miss against the embedder or LLM on window
  rollover). No knob: the jitter is always on and only ever *extends* the TTL.
- **Embedding single-flight** — concurrent misses for the *same single text*
  (the stampede-prone shape: many requests embedding the same query) are
  coalesced in `CachedEmbedder.encode`: only the first caller runs the model;
  the others await the same in-flight result. Batch encodes are untouched to
  preserve model-level batching.

## LLM call deadline propagation

`LoopLimits.max_seconds` gives an orchestrated request a wall-clock deadline;
since the 2026-07 pass it is enforced **per provider call**, not just between
loop ticks: every non-streaming `LLMService` generation (plain and structured)
is awaited through the ambient `LoopBudget`'s `remaining_seconds()`, so the
per-call timeout shrinks as the request ages and a single slow provider call
can no longer outlive the request deadline. An overrun cancels the underlying
call and raises `BudgetExceededError("max_seconds")` — the same signal the
loop's `tick()` raises. Outside an orchestrated request (no ambient budget or
no `max_seconds`) nothing changes. The static SDK timeout
(`LLM_REQUEST_TIMEOUT`, default 120 s) still applies as the outer bound, and
now covers **all** providers: Ollama and HuggingFace clients previously had no
deadline and could pin a worker on a hung local server.

## Semantic-cache & recall memoization

Three allocation/CPU hot spots removed in the 2026-07 pass, all
behavior-preserving:

- **Semantic LLM cache** — the per-tenant stacked embedding matrix is now
  cached and reused across similarity lookups; any write/eviction/expiry
  invalidates it under the same lock. Previously every lookup re-allocated an
  `(entries × dim)` matrix. Expiry sweeps are additionally **interval-gated**
  (at most one full scan per tenant per 60 s, matching `TTLCache`); reads stay
  exact via a per-entry timestamp check, so an expired entry is never served
  between sweeps.
- **Hierarchical memory BM25** — the keyword pass of hybrid recall memoizes
  per-document token statistics (content-keyed) and reuses the whole index
  when the corpus is unchanged between recalls. Scoring is bit-identical to a
  fresh build (`BM25Index.index_tokenized`).
- **Consolidation embeddings** — memory clustering now issues one batched
  `encode()` over all items instead of N per-item model calls.

## Checkpoint serialization

`PostgresCheckpointStore.save` uses **orjson** (~10–20× faster than stdlib
`json`) and, once the payload exceeds **256 KB**, pushes the dump off the
event loop with `asyncio.to_thread`. Serialization stays strict (no
`default=` fallback): a non-JSON-serializable step result fails loudly,
exactly as before.

On top of that, per-tool-step persistence is now **incremental**:
`CheckpointManager.run_step` calls the store's optional `save_step()`
fast-path, which patches only the new step entry, its trajectory element and
the scalar bookkeeping fields into the JSONB row (`jsonb_set`) instead of
re-serializing the whole accumulated state. Cumulative bytes written over an
n-step run drop from O(n²) to O(n); a `load()` after incremental writes is
byte-identical to one after full saves. Stores implementing only the
`CheckpointStore` protocol (no `save_step`) keep working through the full
`save()` path; the first write of a fresh run also falls back to the full
upsert.

## Connection-pool drain on shutdown

The FastAPI lifespan shutdown now explicitly closes the shared Postgres
connection pools (`core.db.connection.close_async_pool`) and the shared Redis
pools (`core.cache.redis_cache.close_redis_pools`) instead of relying on
garbage collection. uvicorn drains in-flight requests before running lifespan
shutdown, so the close is safe and rolling deploys release server-side
DB/Redis resources promptly. `core/resilience/shutdown.py` (`GracefulShutdown`)
is deliberately **not** installed inside the FastAPI app: it would replace
uvicorn's own SIGTERM/SIGINT handlers; it remains available for standalone
(non-uvicorn) embeddings of the runtime.

## Per-request auth memo (quota + route auth)

When quotas are enabled, `QuotaMiddleware` authenticates the request before the
route's own auth dependency runs. The middleware now memoizes the verified
principal in the request scope; `SecurityManager.enforce_auth` reuses it only
when **both** the raw `Authorization` header and the `AuthManager` instance
match, otherwise it re-authenticates from scratch. Net effect: one token
verification per request instead of two, with no trust widening.

## Container build reproducibility

`Dockerfile-slim` / `Dockerfile-full` pin PyTorch to a matched release set
(`torch==2.5.1` + `torchvision==0.20.1` + `torchaudio==2.5.1`, CPU wheels) so the
largest dependency no longer floats between builds. Remaining hardening (tracked
as follow-up): pin the rest of `requirements.txt` from `uv.lock` via
`uv export --frozen` (needs the intended optional-extra set decided) and split the
build into a multi-stage image so the compiler toolchain stays out of the runtime
layer.

## OpenTelemetry GenAI semantic conventions

LLM spans now use the OTel
[GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/):
`gen_ai.operation.name`, `gen_ai.system`, `gen_ai.request.model`,
`gen_ai.request.temperature`/`max_tokens`, and
`gen_ai.usage.input_tokens`/`output_tokens`. App-specific fields (cache hits,
prompt length) live under the `gen_ai.baselith.*` extension namespace. Standard
GenAI observability dashboards light up without custom mapping.

## 2026-07-10 performance additions

- **Retrieval fallbacks are single grouped queries.** Both retrieval fallbacks
  (initial recall *and* the rerank fill) now issue ONE Qdrant
  `query_points_groups` call (`group_by=document_id`, `group_size=1`,
  `must_not` on already-seen ids) instead of one `query_points` per unseen
  indexed document — the fan-out scaled O(corpus size) per chat request.
- **Explicit-doc-match memoization.** The per-request scan that matches query
  text against indexed titles/filenames memoizes each document's lowered
  title/filename/path and stems, keyed by document id and validated against
  the raw metadata triple (an in-place metadata update invalidates only its
  own entry). Previously every chat request paid three `str.lower()` calls
  and up to three `Path()` allocations per indexed document.
- **hiredis parser.** The `redis` dependency now installs the `hiredis` extra;
  redis-py auto-detects it and switches to the C reply parser (largest gains
  on bulk replies such as `mget` of cached LLM payloads). RESP3 (`protocol=3`)
  is deliberately NOT enabled — it changes some reply shapes and buys nothing
  without client-side caching.
- **Idempotency replay via orjson.** The `Idempotency-Key` middleware
  serializes/parses stored responses with orjson; entries written by the
  previous stdlib-json code still parse.
- **Token estimation off-loop.** `estimate_tokens_async` mirrors
  `estimate_tokens` but runs the exact tiktoken encode in a worker thread for
  texts over 64 KiB (the LLM service uses it on all request paths); small
  prompts stay inline where a thread hop would cost more than it saves.
- **Lazy SQL-text stringification.** The cost-control DB tracking passes the
  raw psycopg query object through and stringifies only when a positive
  `SQL_QUERY_LIMIT` actually consumes the text.
