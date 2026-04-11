"""
Chat Router.

Provides endpoints for synchronous and streaming chat interactions with the agent.
Integrates rate limiting and observability.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from core.chat import chat_service
from core.models.chat import ChatRequest
from core.middleware import require_user

router = APIRouter(dependencies=[Depends(require_user)])


@router.post("/chat")
async def chat(req: ChatRequest):
    """
    Main endpoint for querying the agent.
    Delegated to ChatService which handles retrieval, reranking, caching, and response generation.
    """
    return await chat_service.handle_chat_async(req)


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """Returns the agent response as a stream (text chunks)."""

    stream = await chat_service.handle_chat_stream_async(req)
    return StreamingResponse(
        stream,
        media_type="text/plain",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
