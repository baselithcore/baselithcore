"""
RAG System - Retrieval-Augmented Generation on BaselithCore services.

Ingests text into the framework's vector store (Qdrant by default) and answers
questions from the retrieved passages with the configured LLM. Every setting
comes from ``.env`` (``LLM_PROVIDER``, ``LLM_MODEL``, ``VECTORSTORE_*``,
``HOST``/``PORT``), which ``baselith init`` generated for local development.

The services are built on first use, so the server starts — and ``/health``
answers — before Qdrant or the model is reachable; ``/ingest`` and ``/query``
report the failure instead.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from core.config import get_app_config, get_llm_config, get_vectorstore_config
from core.models.domain import Document
from core.observability.logging import get_logger

logger = get_logger(__name__)


# ============================================================================
# Models
# ============================================================================


class IngestRequest(BaseModel):
    """Text to add to the knowledge base."""

    content: str = Field(..., min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    collection: str | None = Field(None, description="Target collection")


class QueryRequest(BaseModel):
    """A question for the knowledge base."""

    query: str = Field(..., min_length=1, description="The user query")
    collection: str | None = Field(None, description="Target collection")
    top_k: int = Field(5, ge=1, le=20, description="Passages to retrieve")


class QueryResponse(BaseModel):
    """The answer and the passages it was built from."""

    answer: str
    sources: list[dict[str, Any]]


# ============================================================================
# RAG pipeline
# ============================================================================


class RAGSystem:
    """Ingest and answer, on the framework's embedder, vector store and LLM."""

    async def ingest(self, request: IngestRequest) -> dict[str, Any]:
        """Embed and index one document."""
        from core.services.vectorstore import get_vectorstore_service

        document = Document(
            id=str(uuid.uuid4()), content=request.content, metadata=request.metadata
        )
        indexed = await get_vectorstore_service().index(
            [document], collection_name=request.collection
        )
        return {"status": "success", "document_id": document.id, "indexed": indexed}

    async def query(self, request: QueryRequest) -> QueryResponse:
        """Retrieve the closest passages and answer from them."""
        from core.nlp.models import get_embedder
        from core.services.llm import get_llm_service
        from core.services.vectorstore import get_vectorstore_service

        vector = await get_embedder().encode_query(request.query)
        results = await get_vectorstore_service().search(
            query_vector=list(vector),  # type: ignore[arg-type]
            k=request.top_k,
            collection_name=request.collection,
            query_text=request.query,
        )
        context = "\n\n".join(result.document.content for result in results)
        prompt = (
            "Answer the question using only the context below. If the context "
            "does not contain the answer, say so.\n\n"
            f"Context:\n{context}\n\nQuestion: {request.query}"
        )
        answer = await get_llm_service().generate_response(prompt)
        return QueryResponse(
            answer=answer,
            sources=[
                {
                    "id": result.document.id,
                    "score": result.score,
                    "metadata": result.document.metadata,
                }
                for result in results
            ],
        )


# ============================================================================
# FastAPI Application
# ============================================================================

rag = RAGSystem()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Validate the configuration at startup; services connect on first use."""
    llm = get_llm_config()
    vectorstore = get_vectorstore_config()
    logger.info(
        "RAG system starting: llm=%s/%s vectorstore=%s",
        llm.provider,
        llm.model,
        vectorstore.provider,
    )
    yield
    logger.info("RAG system stopped")


app = FastAPI(
    title="Baselith RAG System",
    description="Retrieval-Augmented Generation powered by BaselithCore",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness: the process is up (services are checked on use)."""
    return {"status": "healthy"}


@app.post("/ingest")
async def ingest(request: IngestRequest) -> dict[str, Any]:
    """Ingest a document into the knowledge base."""
    try:
        return await rag.ingest(request)
    except Exception as exc:
        logger.exception("Ingestion failed")
        raise HTTPException(
            status_code=503, detail=f"Ingestion failed: {type(exc).__name__}"
        ) from exc


@app.post("/query", response_model=QueryResponse)
async def query(request: QueryRequest) -> QueryResponse:
    """Query the knowledge base."""
    try:
        return await rag.query(request)
    except Exception as exc:
        logger.exception("Query failed")
        raise HTTPException(
            status_code=503, detail=f"Query failed: {type(exc).__name__}"
        ) from exc


# ============================================================================
# Entry Point
# ============================================================================

if __name__ == "__main__":
    # HOST/PORT from .env (127.0.0.1:8000 in the development profile
    # `baselith init` writes): a laptop's dev server stays off the LAN.
    _app_config = get_app_config()
    uvicorn.run(app, host=_app_config.host, port=_app_config.port)
