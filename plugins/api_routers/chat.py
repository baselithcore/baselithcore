"""
Chat Router.

Provides endpoints for synchronous and streaming chat interactions with the agent.
Integrates rate limiting and observability.
"""

from __future__ import annotations

from typing import AsyncIterator

from fastapi import APIRouter, Depends, Response
from fastapi.responses import StreamingResponse

from core.chat import chat_service
from core.middleware import require_user
from core.models.chat import ChatRequest, ChatResponse
from core.observability.logging import get_logger
from core.transparency import TransparencyService, get_transparency_service

logger = get_logger(__name__)

# Header signalling that an AI-interaction disclosure applies (Art 50(1)). A
# fixed token keeps the value header-safe regardless of the configured text;
# the full notice rides in the response body metadata.
_DISCLOSURE_HEADER = "X-Baselith-AI-Disclosure"

# Hard cap on streamed response size in bytes (~4MB). Protects against
# unbounded memory growth from runaway LLM generations.
_STREAM_MAX_BYTES = 4 * 1024 * 1024
# Hard cap per-chunk size to prevent single oversized chunk DoS.
_STREAM_MAX_CHUNK_BYTES = 64 * 1024

router = APIRouter(dependencies=[Depends(require_user)])


def _apply_transparency(resp: ChatResponse, svc: TransparencyService) -> dict[str, str]:
    """Attach Article 50 disclosure + provenance to a chat response.

    No-op unless transparency is enabled. Disclosure (Art 50(1)) is written into
    ``metadata.ai_disclosure`` (body, additive — ``ChatResponse`` allows extra)
    and signalled by a header; a provenance tag (Art 50(2)) over the answer text
    is returned as the ``X-Baselith-AI-Provenance`` header. Returns the headers to
    set on the HTTP response.
    """
    if not svc.enabled:
        return {}
    headers: dict[str, str] = {}
    if svc.should_disclose():
        notice = svc.disclosure_notice()
        meta = dict(resp.metadata or {})
        meta["ai_disclosure"] = notice.to_dict()
        resp.metadata = meta
        headers[_DISCLOSURE_HEADER] = "true"
    tag = svc.mark_content(resp.answer)
    name, value = svc.provenance_header(tag)
    headers[name] = value
    return headers


@router.post("/chat")
async def chat(req: ChatRequest, response: Response):
    """
    Main endpoint for querying the agent.
    Delegated to ChatService which handles retrieval, reranking, caching, and response generation.
    """
    result = await chat_service.handle_chat_async(req)
    for key, value in _apply_transparency(result, get_transparency_service()).items():
        response.headers[key] = value
    return result


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
    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",  # Disable nginx buffering
    }
    # Art 50(1): signal AI disclosure on the streaming surface. Provenance
    # (Art 50(2)) needs the full output hash and is omitted here to avoid
    # buffering the stream — it is emitted on the non-streaming /chat endpoint.
    svc = get_transparency_service()
    if svc.enabled and svc.should_disclose():
        headers[_DISCLOSURE_HEADER] = "true"
    return StreamingResponse(
        _bounded_stream(stream, _STREAM_MAX_BYTES, _STREAM_MAX_CHUNK_BYTES),
        media_type="text/plain",
        headers=headers,
    )
