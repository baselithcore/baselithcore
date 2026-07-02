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
