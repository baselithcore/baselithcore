"""WebSocket chat endpoint.

Persistent conversational channel over one connection: the client sends
``{"query": ..., "conversation_id": ...}`` frames and receives
``{"type": "chunk"|"final"|"error", ...}`` frames per turn. SSE
(``POST /chat/stream``) remains the one-shot streaming surface.

Authentication happens at the handshake — the same credentials as the REST
chat surface (``Authorization: Bearer ...``/``ApiKey ...`` or ``x-api-key``);
an unauthenticated handshake is closed with **4401** before any model spend.
Cross-site WebSocket hijacking is rejected upstream by the CSWSH origin guard
in :mod:`core.middleware.csrf`. Each turn's stream goes through the same
size guards as the SSE surface.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.observability.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["chat"])

#: Close code for a failed handshake authentication (4000-range = app-defined).
WS_CLOSE_UNAUTHENTICATED = 4401

#: Cap on one inbound query frame — ``ChatRequest``'s own ``max_length``, so
#: an oversized frame is rejected with an error frame (REST parity: never
#: silently truncated).
MAX_QUERY_CHARS = 8000


def _get_auth_manager() -> Any:
    from core.auth.manager import get_auth_manager

    return get_auth_manager()


def _get_chat_service() -> Any:
    from core.chat import chat_service

    return chat_service


async def _authenticate(websocket: WebSocket) -> Any | None:
    """Resolve the handshake's identity; None when anonymous."""
    auth_header = websocket.headers.get("authorization")
    if not auth_header:
        api_key = websocket.headers.get("x-api-key")
        if api_key:
            auth_header = f"ApiKey {api_key}"
    user = await _get_auth_manager().authenticate(auth_header)
    return user if user is not None and user.is_authenticated else None


@router.websocket("/chat/ws")
async def chat_ws(websocket: WebSocket) -> None:
    """Conversational WebSocket: one authenticated connection, many turns."""
    user = await _authenticate(websocket)
    if user is None:
        # Close BEFORE accept: the handshake is rejected outright.
        await websocket.close(
            code=WS_CLOSE_UNAUTHENTICATED, reason="Authentication required"
        )
        return

    await websocket.accept()
    from pydantic import ValidationError

    from core.models.chat import ChatRequest
    from plugins.api_routers.chat import (
        STREAM_MAX_BYTES,
        STREAM_MAX_CHUNK_BYTES,
        bounded_stream,
    )

    chat_service = _get_chat_service()
    try:
        while True:
            payload = await websocket.receive_json()
            query = str(payload.get("query") or "").strip()
            if not query:
                await websocket.send_json(
                    {"type": "error", "detail": "query is required"}
                )
                continue
            if len(query) > MAX_QUERY_CHARS:
                await websocket.send_json(
                    {
                        "type": "error",
                        "detail": f"query exceeds {MAX_QUERY_CHARS} characters",
                    }
                )
                continue
            try:
                request = ChatRequest(
                    query=query,
                    conversation_id=payload.get("conversation_id"),
                )
            except ValidationError as exc:
                # A malformed frame must cost one error frame, never the
                # connection.
                await websocket.send_json(
                    {
                        "type": "error",
                        "detail": f"invalid request: {exc.error_count()} field error(s)",
                    }
                )
                continue
            stream = await chat_service.handle_chat_stream_async(request)
            async for chunk in bounded_stream(
                stream, STREAM_MAX_BYTES, STREAM_MAX_CHUNK_BYTES
            ):
                await websocket.send_json({"type": "chunk", "content": chunk})
            await websocket.send_json({"type": "final"})
    except WebSocketDisconnect:
        logger.debug("chat_ws_disconnected", extra={"user_id": user.user_id})


__all__ = ["router"]
