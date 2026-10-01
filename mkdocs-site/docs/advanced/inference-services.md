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
