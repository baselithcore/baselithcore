---
title: Inference Services
description: Embedding, reranking and vector search as core services, so plugins load no models and open no Qdrant client
---

Embedding, reranking and vector search are **services of the core**. A plugin
calls them; it does not carry its own `torch` model or Qdrant connection.

The reason is memory. An API worker that loads a sentence-embedding model, a
cross-encoder and an embedded Qdrant holds several GiB by itself, and every
extra worker repeats it. With the services remote, the API process imports no
ML runtime at all, and the models are scaled — and sized — where they belong:
as their own servers.

All three live in `core.services.inference`. Importing it loads no model and
opens no connection; `torch` is imported only inside the explicit `local`
backends.

## EmbeddingService

```python
from core.di.lazy_registry import get_lazy_registry

embeddings = await get_lazy_registry().get_or_create("embedding")
vectors = await embeddings.embed_documents(["first", "second"])
query = await embeddings.embed_query("what is covered?")
embeddings.model, embeddings.dim   # "BAAI/bge-m3", 1024
```

A plugin asks for the service by declaring `embedding` (and `rerank`,
`qdrant`) in its manifest's `required_resources` or `optional_resources`; the
core then registers the factory, builds it on first use and closes it at
shutdown.

| Variable | Default | Meaning |
| --- | --- | --- |
| `BASELITH_EMBEDDING_BACKEND` | `remote` | `remote` (TEI) or `local` |
| `BASELITH_EMBEDDING_URL` | — | TEI base URL; required for `remote` |
| `BASELITH_EMBEDDING_MODEL` | `BAAI/bge-m3` | model id |
| `BASELITH_EMBEDDING_DIM` | `1024` | vector size |
| `BASELITH_EMBEDDING_BATCH_SIZE` | `32` | texts per HTTP request |
| `BASELITH_EMBEDDING_TIMEOUT` | `60` | per-request timeout, seconds |
| `BASELITH_EMBEDDING_MAX_RETRIES` | `3` | retries on 5xx, 429, timeout |
| `BASELITH_EMBEDDING_API_KEY` | — | optional bearer token |

The `remote` backend posts `{"inputs": [...]}` to TEI's `/embed` over one
shared `httpx.AsyncClient`, retries 5xx, 429, timeouts and connection errors
with exponential backoff, and fails at once on any other 4xx. The `local`
backend is a development opt-in: sentence-transformers, loaded lazily as one
singleton per process.

## RerankService

```python
rerank = await get_lazy_registry().get_or_create("rerank")
top = await rerank.rerank("what is covered?", passages, top_k=5)
# [(index_into_passages, score), ...] best first
```

Same shape, with `BASELITH_RERANK_*` (`MODEL` defaults to
`BAAI/bge-reranker-v2-m3`). `BASELITH_RERANK_MAX_CANDIDATES` (default 100) caps
how many passages are scored: the first N are, the rest are dropped.

## Scoped vector store

```python
runtime = await get_lazy_registry().get_or_create("qdrant")
store = runtime.for_plugin(self)      # scope: plugin name + self.tenant_key()
await store.create_collection("docs", vectors_config=...)
await store.upsert("docs", points)
hits = await store.search("docs", vector, limit=10)
```

There is one `AsyncQdrantClient` per process, in **server mode**
(`BASELITH_QDRANT_URL`). The embedded `path=` mode is refused, and `:memory:`
is accepted only by tests. A plugin never receives the client: it receives a
`ScopedVectorStore`, which takes **logical** collection names and maps them to
`<tenant>.<plugin>.<name>` itself.

- No part of that name may contain `.`, so no `(tenant, plugin, name)` triple
  can produce another triple's collection.
- A tenant key outside `[A-Za-z0-9_-]` (an email, a prefixed id) is replaced by
  a digest; an empty key raises `TenantScopeError` — vector access fails closed.
- `list_collections()` returns only this scope's collections.
- `BASELITH_QDRANT_PREFER_GRPC=true` switches data calls to gRPC, which is what
  multivector (ColBERT) payloads need.

## Synchronous plugins

Some plugins are synchronous end to end. `get_sync_inference()` returns a
bridge that owns **one dedicated event loop in a daemon thread**, builds its
own service instances on it on first use, and lets any thread call
`embed_documents`, `embed_query`, `rerank` and `store(plugin, tenant)` as
ordinary blocking functions. It never uses the application's loop (which
would deadlock a caller already running on it) and never creates a loop per
call (which would build a throw-away connection pool each time). The
lifespan closes it at shutdown.

## Keeping plugins honest

`scripts/check_no_inprocess_ml.py` (pre-commit hook `no-inprocess-ml`, run by
the CI pre-commit job) fails when code under `plugins/` calls
`SentenceTransformer(`, `CrossEncoder(`, `BGEM3FlagModel`, `FlagReranker`,
`DocumentConverter(`, `QdrantClient(` or `AsyncQdrantClient(`. Plugins that
have not migrated yet are listed, each with a reason, in
`configs/inprocess_ml_allowlist.yaml`. The list can only shrink: an entry that
no longer matches a flagged call fails the gate.

## Local stack

`docker-compose.core.yml` runs two Hugging Face Text Embeddings Inference
containers (CPU image, models cached in the `tei_cache` volume) next to
Qdrant: `tei-embed` for `BAAI/bge-m3` and `tei-rerank` for
`BAAI/bge-reranker-v2-m3`, and points the API at them.

## Kubernetes

The Helm chart carries the three endpoints in its `inference` block.

```yaml
inference:
  qdrant:
    url: http://qdrant.data.svc:6333      # yours: the chart deploys no Qdrant
  tei:
    enabled: true                         # chart-deployed model servers
    hfTokenSecret: {name: hf-token}       # optional, for gated models
```

With `tei.enabled` the chart deploys one Deployment, Service and model-cache
PVC per model (`BAAI/bge-m3` and `BAAI/bge-reranker-v2-m3`) and writes
`BASELITH_EMBEDDING_URL` and `BASELITH_RERANK_URL` into the ConfigMap that the
API and worker pods read. A key you set yourself under `config` always wins, so
you can point at servers you already run.

- The TEI pods have their own `app.kubernetes.io/name`, so the API Service and
  NetworkPolicy never select them. With `networkPolicy.enabled`, each gets a
  policy that admits only this release's API and worker pods.
- They run non-root with every capability dropped on port 8080, with the model
  cache on a PVC (`persistence.enabled`, default) so a restart does not
  download 2+ GiB again. The first start is slow; the startup probe waits up to
  15 minutes.
- The CPU image is `linux/amd64` only and slow. For real rerank latency use a
  GPU image with `nvidia.com/gpu` in `resources` and a `nodeSelector`.
- Database-style egress policies (`networkPolicy.egress.enabled`) must allow the
  model servers: the `sameNamespace` preset does when they live in the release
  namespace.
- A plugin that still loads its own models (see
  `configs/inprocess_ml_allowlist.yaml`) keeps the old memory footprint until it
  migrates; size the API pod's `resources` for the plugin set you actually run.

## The core's own models

The embedder and reranker the core itself uses — retrieval, memory, the chat
pipeline, `core.services.retrieval.Reranker` — go through the same services.
`core.nlp.models.get_embedder()` and `get_reranker()` return TEI-backed
stand-ins (`core.nlp._remote`) with the sentence-transformers surface
(`encode`, `get_sentence_embedding_dimension`, `predict`) when:

- the backend is `remote` and its URL is set, **and**
- the requested model is the one the server serves (`BASELITH_EMBEDDING_MODEL`,
  `BASELITH_RERANK_MODEL`), or no local sentence-transformers is installed —
  then the served model is used and a `remote_model_substituted` warning names
  both.

Otherwise they load the local model exactly as before. The embedding cache and
its keys are unchanged.

## An image without torch

The `Dockerfile` takes `--build-arg ML_RUNTIME=remote`. It skips torch,
sentence-transformers, FlagEmbedding and accelerate and the model pre-cache,
and fails the build if any other requirement pulls torch in. The default stays
`local`.

```bash
docker build --build-arg ML_RUNTIME=remote -t baselith:remote .
```

Measured on the same tree, with only `auth` and `wikigen` enabled and the
inference services configured: the `remote` image is 7.9 GB against 13.1 GB, and
the API process (one worker) holds about 250 MB RSS after startup, where an
in-process BGE-M3 plus reranker cost 2.8 GB. A plugin listed in
`configs/inprocess_ml_allowlist.yaml` cannot run on this image: disable it, or
build `local`.

## Client hardening

The embedding and rerank clients (`core/services/inference/_http.py`) bound
every call and guard the bearer token. Each knob exists once per service, under
`BASELITH_EMBEDDING_*` and `BASELITH_RERANK_*`:

| Variable suffix | Default | Meaning |
| --- | --- | --- |
| `MAX_TOTAL_SECONDS` | `50` | Budget for one call, retries and backoff included. Each attempt's timeout is clamped to what is left, so the call fails before the edge proxy's read timeout (nginx/ingress default 60 s) instead of surfacing to the user as a `504`. |
| `MAX_RESPONSE_BYTES` | `67108864` (64 MiB) | Largest response body accepted. A larger declared `Content-Length` is refused before reading; otherwise the body is streamed and cut off past the cap. Minimum `1024`. |
| `RETRY_RATE_LIMITED` | `false` | Retry on HTTP `429`. Off by default: TEI answers `429` when its queue is full, and more requests from the interactive path only lengthen the backlog. Enable it for batch indexing jobs. |
| `ALLOW_INSECURE_KEY` | `false` | Send `API_KEY` over plain `http` to a host that may be public. |

**Retries.** Timeouts, connection errors and `408`/`425`/`500`/`502`/`503`/`504`
are retried with exponential backoff until `MAX_RETRIES` or
`MAX_TOTAL_SECONDS` runs out, whichever comes first. `429` is retried only
with `RETRY_RATE_LIMITED=true` — this supersedes the "retries on 5xx, 429"
wording earlier on this page. Any other `4xx` fails at once, and the upstream
error body is never echoed into the raised `InferenceError` (a TEI error text
can name model paths or internal hosts).

**Where the key may travel.** With `API_KEY` set, the client is built only when
the URL is `https`, or plain `http` to a host that cannot be on the public
internet: loopback (`localhost`, `127.0.0.1`, `::1`), a single-label name (a
compose service or same-namespace Kubernetes Service such as
`http://tei-embed:8080`), or a cluster-local name ending in `.svc` or
`.cluster.local`. Anything else raises `InferenceConfigError` when the service
is built, unless `ALLOW_INSECURE_KEY=true` accepts the risk for that service.
Environment proxies (`HTTP_PROXY`, `HTTPS_PROXY`) are ignored by these clients
(`trust_env=False`), so the token cannot be routed through one.

**Qdrant names.** `BASELITH_QDRANT_URL` and `BASELITH_QDRANT_API_KEY` also bind
from the unprefixed `QDRANT_URL` and `QDRANT_API_KEY` that the compose files,
the Helm chart and `configs/.env.*` already set for the vector store. The
prefixed names remain for an inference-only Qdrant and win when both are set.

For the Helm side — the shared TEI bearer token (`inference.tei.apiKeySecret`),
the image digest pin, the TEI egress policy and disruption budget — and for
the compose `inference` profile, see
[Deployment › Inference model servers](deployment.md#inference-model-servers).
