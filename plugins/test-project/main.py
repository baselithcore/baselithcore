"""
RAG System - Complete Retrieval-Augmented Generation Application.

A production-ready RAG system with document ingestion, semantic search,
and LLM-powered response generation.
"""

from core.observability.logging import get_logger
from typing import Optional, Any, Dict
from contextlib import asynccontextmanager

# Baselith-Core Imports
from core.lifecycle import LifecycleMixin, AgentState, AgentError, FrameworkErrorCode
from core.orchestration.protocols import AgentProtocol
from core.context import tenant_context
from core.config import get_llm_config, get_vectorstore_config
from core.di import DependencyContainer
from core.interfaces import LLMServiceProtocol, VectorStoreProtocol

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import uvicorn

logger = get_logger(__name__)


# ============================================================================
# Models
# ============================================================================


class QueryRequest(BaseModel):
    """Query request model."""

    query: str = Field(..., min_length=1, description="The user query")
    collection: Optional[str] = Field(None, description="Target collection")
    top_k: Optional[int] = Field(None, ge=1, le=20, description="Number of results")
    stream: bool = Field(False, description="Enable streaming response")


class QueryResponse(BaseModel):
    """Query response model."""

    answer: str
    sources: list[dict]
    tokens_used: int = 0


class IngestRequest(BaseModel):
    """Document ingestion request."""

    content: str
    metadata: dict = {}
    collection: Optional[str] = None


class Document(BaseModel):
    """Document model."""

    id: str
    content: str
    metadata: dict
    score: float = 0.0


# ============================================================================
# RAG Components
# ============================================================================


class RAGSystem(LifecycleMixin, AgentProtocol):
    """Main RAG system orchestrating all components."""

    def __init__(self, agent_id: str):
        super().__init__()
        self.agent_id = agent_id
        self.llm: Optional[LLMServiceProtocol] = None
        self.vectorstore: Optional[VectorStoreProtocol] = None

    async def _do_startup(self) -> None:
        """
        Dogma II: DI First & Dogma III: Async Everything.
        Resolve dependencies and initialize services.
        """
        logger.info(f"🚀 RAG System {self.agent_id} starting up...")

        container = DependencyContainer()

        try:
            self.llm = container.resolve(LLMServiceProtocol)
            self.vectorstore = container.resolve(VectorStoreProtocol)

            # Additional startup logic for services if needed
            if isinstance(self.llm, LifecycleMixin):
                await self.llm.initialize()
            if isinstance(self.vectorstore, LifecycleMixin):
                await self.vectorstore.initialize()

        except Exception as e:
            logger.error(f"Failed to initialize RAG components: {e}")
            raise AgentError(
                f"Dependency resolution failed: {e}",
                code=FrameworkErrorCode.AGENT_STARTUP_FAILED,
            )

    async def _do_shutdown(self) -> None:
        """Shutdown RAG resources."""
        logger.info(f"🛑 RAG System {self.agent_id} shutting down...")
        if isinstance(self.llm, LifecycleMixin):
            await self.llm.shutdown()
        if isinstance(self.vectorstore, LifecycleMixin):
            await self.vectorstore.shutdown()

    async def ingest(
        self, content: str, metadata: dict = None, collection: str = "default"
    ) -> dict:
        """Ingest a document using the vector store protocol."""
        if self.state != AgentState.READY:
            raise AgentError(
                "RAG System not ready", code=FrameworkErrorCode.AGENT_NOT_READY
            )

        metadata = metadata or {}
        # In real implementation, splitting would be handled by a service or utility
        # For the template, we assume the vectorstore or an ingestion service handles it

        try:
            # This is a simplified call - real protocol might vary
            doc_id = await self.vectorstore.add(
                content=content, metadata=metadata, collection=collection
            )

            return {
                "status": "success",
                "document_id": doc_id,
            }
        except Exception as e:
            raise AgentError(
                f"Ingestion failed: {e}", code=FrameworkErrorCode.PROVIDER_ERROR
            )

    async def execute(
        self, input: str, context: Optional[Dict[str, Any]] = None
    ) -> Any:
        """
        AgentProtocol implementation for querying the RAG system.
        """
        if self.state != AgentState.READY:
            raise AgentError(
                "RAG System not ready", code=FrameworkErrorCode.AGENT_NOT_READY
            )

        context = context or {}
        collection = context.get("collection", "default")
        top_k = context.get("top_k", 5)

        # Dogma VII: Ensure we are in a tenant context
        async with tenant_context(context.get("tenant_id", "default-tenant")):
            try:
                # 1. Retrieve relevant documents
                docs = await self.vectorstore.search(
                    query=input, collection=collection, limit=top_k
                )

                # 2. Build context for LLM
                context_str = "\n\n".join([d.content for d in docs])

                # 3. Generate response
                prompt = f"Using the following context, answer the query: {input}\n\nContext:\n{context_str}"
                answer = await self.llm.generate(prompt)

                return answer

            except Exception as e:
                raise AgentError(
                    f"Execution failed: {e}",
                    code=FrameworkErrorCode.AGENT_EXECUTION_FAILED,
                )


# ============================================================================
# FastAPI Application
# ============================================================================

rag = RAGSystem(agent_id="rag-system-01")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info("🚀 Starting Baselith RAG Application...")

    # Load configs to ensure they are valid
    _ = get_llm_config()
    _ = get_vectorstore_config()

    await rag.initialize()
    yield
    await rag.shutdown()
    logger.info("👋 RAG Application shut down.")


app = FastAPI(
    title="Baselith RAG System",
    description="Production-ready RAG application powered by BaselithCore",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy", "agent_state": rag.state, "agent_id": rag.agent_id}


@app.post("/ingest")
async def ingest(request: IngestRequest):
    """Ingest a document into the knowledge base."""
    try:
        return await rag.ingest(
            content=request.content,
            metadata=request.metadata,
            collection=request.collection or "default",
        )
    except AgentError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")


@app.post("/query", response_model=QueryResponse)
async def query(request: QueryRequest):
    """Query the knowledge base."""
    try:
        answer = await rag.execute(
            input=request.query,
            context={
                "collection": request.collection or "default",
                "top_k": request.top_k or 5,
            },
        )
        return QueryResponse(answer=answer, sources=[])
    except AgentError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {e}")


# ============================================================================
# Entry Point
# ============================================================================

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)  # nosec B104
