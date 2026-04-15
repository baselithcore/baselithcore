"""
Chat Router.

Provides endpoints for synchronous and streaming chat interactions with the agent.
Integrates rate limiting and observability.
"""

from __future__ import annotations

from typing import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from core.chat import chat_service
from core.models.chat import ChatRequest
from core.middleware import require_user
from core.observability.logging import get_logger

logger = get_logger(__name__)

# Hard cap on streamed response size in bytes (~4MB). Protects against
# unbounded memory growth from runaway LLM generations.
_STREAM_MAX_BYTES = 4 * 1024 * 1024
# Hard cap per-chunk size to prevent single oversized chunk DoS.
_STREAM_MAX_CHUNK_BYTES = 64 * 1024

router = APIRouter(dependencies=[Depends(require_user)])


@router.post("/chat")
async def chat(req: ChatRequest):
    """
    Main endpoint for querying the agent.
    Delegated to ChatService which handles retrieval, reranking, caching, and response generation.
    """
    return await chat_service.handle_chat_async(req)


async def _bounded_stream(
    source: AsyncIterator[str],
    max_bytes: int,
    max_chunk_bytes: int,
) -> AsyncIterator[str]:
    """Wrap a chat stream with total and per-chunk size guards."""
    total = 0
    async for chunk in source:
        if not isinstance(chunk, (str, bytes)):
            continue
        data = chunk.encode("utf-8") if isinstance(chunk, str) else chunk
        if len(data) > max_chunk_bytes:
            # Split oversized chunk to cap worst-case memory per emission.
            for i in range(0, len(data), max_chunk_bytes):
                slice_bytes = data[i : i + max_chunk_bytes]
                total += len(slice_bytes)
                if total > max_bytes:
                    logger.warning(
                        "chat_stream_truncated",
                        extra={"limit_bytes": max_bytes, "total_bytes": total},
                    )
                    return
                yield slice_bytes.decode("utf-8", errors="replace")
            continue
        total += len(data)
        if total > max_bytes:
            logger.warning(
                "chat_stream_truncated",
                extra={"limit_bytes": max_bytes, "total_bytes": total},
            )
            return
        yield (
            chunk if isinstance(chunk, str) else data.decode("utf-8", errors="replace")
        )


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """Returns the agent response as a stream (text chunks)."""

    stream = await chat_service.handle_chat_stream_async(req)
    return StreamingResponse(
        _bounded_stream(stream, _STREAM_MAX_BYTES, _STREAM_MAX_CHUNK_BYTES),
        media_type="text/plain",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
