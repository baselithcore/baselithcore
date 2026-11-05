"""
Knowledge base API.

Manage and query the documents behind retrieval-augmented answers: ingest text
or an uploaded file, delete a document, and search with scores (hit testing),
so an integrator can check *what the model would be given* for a question.

Access is gated by capability scopes (``knowledge:read`` / ``knowledge:write``)
on the authenticated identity, on top of the ``require_user`` dependency.
Tenant isolation is the vector store's: every read and write is confined to the
caller's tenant.

Mounted only when ``KNOWLEDGE_API_ENABLED`` is set (see
``ApiRoutersPlugin.get_routers``), so it adds no surface by default.
"""

from __future__ import annotations

import json
import re
import time
from typing import Annotated, Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field

from baselith.core.auth.manager import AuthManager
from baselith.core.config.knowledge import KnowledgeApiConfig, get_knowledge_config
from baselith.core.middleware import require_user
from baselith.core.observability.logging import get_logger
from baselith.core.services.vectorstore.exceptions import VectorStoreError
from baselith.core.services.vectorstore.knowledge import (
    CHUNKING_MODES,
    IngestError,
    delete_document,
    ingest_document,
    search_by_text,
)
from baselith.plugins.api_routers.routes._knowledge_extract import (
    ExtractionFailed,
    UnsupportedFile,
    UploadTooLarge,
    extract_text,
    read_capped,
    safe_filename,
)
from baselith.plugins.api_routers.schemas import problem_responses

logger = get_logger(__name__)

router = APIRouter(
    prefix="/knowledge", tags=["knowledge"], dependencies=[Depends(require_user)]
)

ChunkingMode = Literal["default", "auto", "qa"]

#: Hits one search may ask for.
MAX_TOP_K = 50
_COLLECTION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_MAX_METADATA_KEYS = 50
_MAX_METADATA_BYTES = 16 * 1024


# ----------------------------------------------------------------- models


class KnowledgeDocumentRequest(BaseModel):
    """Text to ingest."""

    content: str = Field(min_length=1, description="The document text")
    document_id: str | None = Field(
        default=None,
        max_length=200,
        description="Your id for the document; ingesting the same id again replaces it. "
        "Generated when omitted",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Extra fields stored with every chunk"
    )
    collection: str | None = Field(default=None, description="An exposed collection")
    chunking: ChunkingMode = Field(
        default="default",
        description="default: by size; auto: by structure (needs source_name); "
        "qa: one chunk per question/answer pair",
    )
    source_name: str | None = Field(
        default=None, max_length=200, description="File name, for chunking=auto"
    )


class KnowledgeDocumentResult(BaseModel):
    """What an ingestion wrote."""

    document_id: str
    collection: str | None
    chunks: int
    chunking: str


class KnowledgeDocumentRemoved(BaseModel):
    """Result of a deletion (idempotent)."""

    document_id: str
    deleted: bool


class KnowledgeSearchRequest(BaseModel):
    """A hit-testing query."""

    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=MAX_TOP_K)
    collection: str | None = None
    rerank: bool = Field(default=False, description="Re-score with the cross-encoder")
    min_score: float | None = Field(default=None, description="Drop hits below this")


class KnowledgeHitView(BaseModel):
    """One retrieved passage."""

    document_id: str
    chunk_index: int | None
    content: str
    score: float
    metadata: dict[str, Any]


class KnowledgeSearchResult(BaseModel):
    """Retrieval outcome for a query."""

    query: str
    collection: str | None
    reranked: bool
    took_ms: float
    count: int
    hits: list[KnowledgeHitView]


# ---------------------------------------------------------------- helpers


def _enforce(request: Request, scope: str) -> None:
    user = getattr(request.state, "user", None)
    AuthManager.enforce_scopes(user, scope)


def _collection(config: KnowledgeApiConfig, name: str | None) -> str | None:
    """Validate a client-named collection against the exposed list."""
    if name is None:
        return None
    if not _COLLECTION.match(name) or name not in config.collections:
        raise HTTPException(
            status_code=404, detail=f"Collection '{name}' is not exposed."
        )
    return name


def _metadata(raw: dict[str, Any]) -> dict[str, Any]:
    from baselith.core.services.vectorstore._indexing import RESERVED_PAYLOAD_KEYS

    if len(raw) > _MAX_METADATA_KEYS:
        raise HTTPException(
            status_code=422, detail=f"At most {_MAX_METADATA_KEYS} metadata keys."
        )
    clash = sorted(RESERVED_PAYLOAD_KEYS.intersection(raw))
    if clash:
        raise HTTPException(
            status_code=422,
            detail=f"Metadata keys are reserved by the store: {', '.join(clash)}.",
        )
    if any(not isinstance(k, str) or not 0 < len(k) <= 100 for k in raw):
        raise HTTPException(
            status_code=422, detail="Metadata keys must be short strings."
        )
    try:
        size = len(json.dumps(raw, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Metadata must be JSON.") from exc
    if size > _MAX_METADATA_BYTES:
        raise HTTPException(
            status_code=422, detail=f"Metadata larger than {_MAX_METADATA_BYTES} bytes."
        )
    return raw


async def _ingest(
    config: KnowledgeApiConfig,
    *,
    content: str,
    document_id: str | None,
    metadata: dict[str, Any],
    collection: str | None,
    chunking: str,
    source_name: str | None,
) -> dict[str, Any]:
    if len(content) > config.max_text_chars:
        raise HTTPException(
            status_code=413,
            detail=f"Text longer than {config.max_text_chars} characters.",
        )
    try:
        result = await ingest_document(
            content,
            document_id=document_id,
            metadata=_metadata(metadata),
            collection=_collection(config, collection),
            chunking=chunking,
            source_name=source_name,
        )
    except IngestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except VectorStoreError as exc:
        logger.error("knowledge_ingest_store_failed error=%s", exc)
        raise HTTPException(
            status_code=503, detail="The knowledge store is unavailable."
        ) from exc
    return {
        "document_id": result.document_id,
        "collection": result.collection,
        "chunks": result.chunks,
        "chunking": result.chunking,
    }


# ----------------------------------------------------------------- routes


@router.post(
    "/documents",
    status_code=status.HTTP_201_CREATED,
    response_model=KnowledgeDocumentResult,
    responses=problem_responses(401, 403, 404, 413, 422, 503),
)
async def create_document(
    body: KnowledgeDocumentRequest, request: Request
) -> dict[str, Any]:
    """Ingest text. Re-posting a ``document_id`` replaces that document."""
    _enforce(request, "knowledge:write")
    return await _ingest(
        get_knowledge_config(),
        content=body.content,
        document_id=body.document_id,
        metadata=body.metadata,
        collection=body.collection,
        chunking=body.chunking,
        source_name=body.source_name,
    )


@router.post(
    "/documents/upload",
    status_code=status.HTTP_201_CREATED,
    response_model=KnowledgeDocumentResult,
    responses=problem_responses(401, 403, 404, 413, 415, 422, 503),
)
async def upload_document(
    request: Request,
    file: Annotated[
        UploadFile, File(description="txt, md, csv, tsv, json, pdf, docx, xlsx or pptx")
    ],
    document_id: Annotated[str | None, Form(max_length=200)] = None,
    collection: Annotated[str | None, Form()] = None,
    chunking: Annotated[ChunkingMode, Form()] = "default",
    metadata: Annotated[str, Form(description="A JSON object")] = "{}",
) -> dict[str, Any]:
    """Ingest an uploaded file.

    The type comes from the file *extension*; the name is kept only as a label
    (``filename`` in the metadata) and to pick the splitter for
    ``chunking=auto``. Text is extracted from the bytes alone.
    """
    _enforce(request, "knowledge:write")
    config = get_knowledge_config()
    try:
        extra = json.loads(metadata or "{}")
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="metadata must be a JSON object."
        ) from exc
    if not isinstance(extra, dict):
        raise HTTPException(status_code=422, detail="metadata must be a JSON object.")

    filename = safe_filename(file.filename)
    try:
        data = await read_capped(file, config.max_upload_bytes)
        text = await extract_text(data, filename)
    except UploadTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except UnsupportedFile as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except ExtractionFailed as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return await _ingest(
        config,
        content=text,
        document_id=document_id,
        metadata={"filename": filename, **extra},
        collection=collection,
        chunking=chunking,
        source_name=filename,
    )


@router.delete(
    "/documents/{document_id}",
    response_model=KnowledgeDocumentRemoved,
    responses=problem_responses(401, 403, 404, 422, 503),
)
async def remove_document(
    document_id: str, request: Request, collection: str | None = None
) -> dict[str, Any]:
    """Delete every chunk of a document. Deleting an unknown id succeeds."""
    _enforce(request, "knowledge:write")
    try:
        await delete_document(
            document_id, collection=_collection(get_knowledge_config(), collection)
        )
    except IngestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except VectorStoreError as exc:
        logger.error("knowledge_delete_store_failed error=%s", exc)
        raise HTTPException(
            status_code=503, detail="The knowledge store is unavailable."
        ) from exc
    return {"document_id": document_id, "deleted": True}


@router.post(
    "/search",
    response_model=KnowledgeSearchResult,
    responses=problem_responses(401, 403, 404, 422, 503),
)
async def search(body: KnowledgeSearchRequest, request: Request) -> dict[str, Any]:
    """Hit testing: the passages retrieval would return for a query, with scores.

    ``score`` is the vector similarity (or the cross-encoder score when
    ``rerank`` is set). An empty ``hits`` list means nothing relevant was
    found; a store failure answers 503 instead of an empty list.
    """
    _enforce(request, "knowledge:read")
    collection = _collection(get_knowledge_config(), body.collection)
    started = time.perf_counter()
    try:
        hits = await search_by_text(
            body.query,
            top_k=body.top_k,
            collection=collection,
            rerank=body.rerank,
            min_score=body.min_score,
        )
    except VectorStoreError as exc:
        logger.error("knowledge_search_store_failed error=%s", exc)
        raise HTTPException(
            status_code=503, detail="The knowledge store is unavailable."
        ) from exc
    return {
        "query": body.query,
        "collection": collection,
        "reranked": body.rerank,
        "took_ms": round((time.perf_counter() - started) * 1000, 1),
        "count": len(hits),
        "hits": [hit.to_dict() for hit in hits],
    }


__all__ = ["CHUNKING_MODES", "router"]
