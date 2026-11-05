---
title: Knowledge API
description: Ingest documents, delete them and test retrieval with scores over HTTP
---

The knowledge API manages the documents behind retrieval-augmented answers and
lets you check **what the model would be given** for a question: ingest text or
an uploaded file, delete a document, and search with scores (hit testing).

It is **opt-in**: with `KNOWLEDGE_API_ENABLED` unset no route is mounted.

**Modules**: `baselith/plugins/api_routers/routes/knowledge.py` (the router), and
`baselith/core/services/vectorstore/knowledge.py` plus `qa_splitter.py` (the
logic, shared with the MCP `index_document` tool and the workflow `RETRIEVE`
node; see [Services › Text-level helpers](services.md#text-level-helpers)).

## Enabling

```bash
KNOWLEDGE_API_ENABLED=true
KNOWLEDGE_COLLECTIONS=faq,manuals      # collections clients may name (besides the default one)
KNOWLEDGE_MAX_UPLOAD_BYTES=10485760    # per file
KNOWLEDGE_MAX_TEXT_CHARS=2000000       # per document, posted or extracted
```

Every variable is in the [configuration
reference](../getting-started/configuration.md#knowledge-api-configuration).

## Access

Routes require an authenticated identity (`require_user`) **and** a capability
scope:

| Routes | Scope |
| ------ | ----- |
| `POST /knowledge/search` | `knowledge:read` |
| `POST /knowledge/documents`, `POST /knowledge/documents/upload`, `DELETE /knowledge/documents/{id}` | `knowledge:write` |

By default the `user` and `job` roles carry `knowledge:read`, `service` carries
`knowledge:*`, and `admin` everything; a least-privilege **scoped API key** gets
exactly the scopes it is minted with. See [Auth › Default role → scope
map](auth.md#default-role-scope-map).

Isolation between tenants is the vector store's: every read, write and delete is
confined to the caller's tenant. Document ids are per tenant, so two tenants
can use the same id without touching each other's chunks.

### Collections

A client may name a `collection` only if the operator listed it in
`KNOWLEDGE_COLLECTIONS`; the configured default collection is always available
when no collection is named. Anything else answers **404**. The restriction is
deliberate: a client that could name any collection could write into the ones
that hold agent memory, or create new ones at will.

## Ingest text

```bash
curl -X POST http://localhost:8000/v1/knowledge/documents \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"document_id": "returns-policy", "content": "Items can be returned within 30 days…",
       "metadata": {"team": "support"}, "chunking": "default"}'
# 201 → {"document_id": "returns-policy", "collection": null, "chunks": 4, "chunking": "default"}
```

- **`document_id`** is yours: 1 to 200 characters of letters, digits, `.`, `_`,
  `:` or `-`, starting with a letter or digit. Posting the same id again
  **replaces** the document; a shorter new version leaves no stale chunks. When
  omitted, an id is generated and returned.
- **`metadata`** is stored with every chunk. At most 50 keys of up to 100
  characters and 16 KiB in total. Keys the pipeline owns (`text`, `source`,
  `document_id`, `tenant_id`, `chunk_index`, …) are **refused with 422**, not
  silently dropped.
- **`chunking`**:

  | Mode | Behaviour |
  | ---- | --------- |
  | `default` | Split by size (800 characters, 200 overlap) |
  | `auto` | Split by structure when `source_name` says what the text is: markdown headings (the heading path is kept as metadata) or python units; plain text falls back to `default` |
  | `qa` | One chunk per question/answer pair, embedding the question ([details](services.md#questionanswer-chunking)); refused with 422 when no pair is found |

- A document over `KNOWLEDGE_MAX_TEXT_CHARS` answers **413**.

## Upload a file

```bash
curl -X POST http://localhost:8000/v1/knowledge/documents/upload \
  -H "Authorization: Bearer $KEY" \
  -F file=@faq.csv -F chunking=qa -F document_id=faq -F 'metadata={"team": "support"}'
```

Accepted: `.txt`, `.md`, `.markdown`, `.rst`, `.csv`, `.tsv`, `.log`, `.json`
(UTF-8 text), and `.pdf`, `.docx`, `.xlsx`, `.pptx` through the `document_sources`
readers when that plugin is installed (a scanned PDF needs OCR configured, or the
upload answers 422 "no text could be extracted"). Anything else answers **415**.

Nothing about the file is trusted:

- The **extension** picks the reader, not the `Content-Type`.
- The **name** is reduced to a basename and used only as a label (`filename` in
  the metadata) and to pick the splitter for `chunking=auto`.
- The body is read in chunks and refused with **413** the moment it passes
  `KNOWLEDGE_MAX_UPLOAD_BYTES`, before it is buffered.
- Rich formats are parsed from a private temporary directory, under a fixed name,
  with owner-only permissions, and removed afterwards. OOXML archives pass the
  readers' zip-bomb screen first.
- Text that looks binary or is not UTF-8 answers 415.

## Delete a document

`DELETE /knowledge/documents/{document_id}?collection=…` removes every chunk of
the document in the caller's tenant. It is **idempotent**: deleting an id that
does not exist succeeds.

## Hit testing

```bash
curl -X POST http://localhost:8000/v1/knowledge/search \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"query": "how long do I have to return an item?", "top_k": 5, "rerank": true}'
```

```json
{
  "query": "how long do I have to return an item?",
  "collection": null,
  "reranked": true,
  "took_ms": 41.3,
  "count": 2,
  "hits": [
    {"document_id": "returns-policy", "chunk_index": 0,
     "content": "Items can be returned within 30 days…", "score": 0.83,
     "metadata": {"team": "support", "source": "returns-policy"}}
  ]
}
```

- `top_k` is 1 to 50 (default 5). `min_score` drops weaker hits. `rerank` re-scores
  the candidates with the cross-encoder, and then `score` is that model's score.
- `count: 0` with an empty `hits` means **nothing relevant was found**. A store
  failure answers **503** instead, never an empty list.
- Internal fields (`tenant_id`, the raw chunk body) are not returned.

## Errors

| Status | Meaning |
| ------ | ------- |
| 401 / 403 | Not authenticated / the identity lacks the scope |
| 404 | The collection is not exposed |
| 413 | Text or upload over its limit |
| 415 | Unsupported or binary file |
| 422 | Invalid id, mode, metadata or JSON; no Q&A pairs; no text extracted |
| 503 | The vector store failed. The response never carries the store's own error text |

## Not offered

There is **no list-documents endpoint**. A vector store has no "distinct
document" query, and listing chunks would scatter one document across many pages
(chunk ids are hashes). Keep your own ids and manage documents by id: ingesting
an id replaces it, deleting an id removes it.
