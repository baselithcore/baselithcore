"""WebSocket chat endpoint.

Persistent conversational channel over one connection: the client sends
``{"query": ..., "conversation_id": ...}`` frames and receives
``{"type": "chunk"|"final"|"error", ...}`` frames per turn. SSE
(``POST /chat/stream``) remains the one-shot streaming surface.

Authorization is the REST chat gate, not a weaker look-alike: the handshake
runs the same ``require_user`` policy as ``POST /chat`` (same credentials —
``Authorization: Bearer ...``/``ApiKey ...`` or ``x-api-key`` — same allowed
roles, same per-identity rate limit and the same per-IP throttle on failed
credentials), and a rejected handshake is closed with **4401**/**4403**/
**4429** before any model spend. The gate runs again on *every turn*, so one
WebSocket turn is metered exactly like one REST request: a rate-limited turn
costs an error frame (the connection survives), while a credential that
expired or was revoked mid-session closes the socket. The HTTP body-size and
quota middlewares do not see WebSocket scopes, so the per-turn gate is what
keeps a long-lived connection from becoming an unmetered channel.
Cross-site WebSocket hijacking is rejected upstream by the CSWSH origin guard
in :mod:`core.middleware.csrf`. Each turn's stream goes through the same
size guards as the SSE surface.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from core.observability.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["chat"])

#: Close codes for a rejected handshake (4000-range = app-defined). The
#: HTTP status the gate would have answered with, offset by 4000, so a client
#: can tell "log in again" (4401) from "slow down" (4429).
WS_CLOSE_UNAUTHENTICATED = 4401
WS_CLOSE_FORBIDDEN = 4403
WS_CLOSE_RATE_LIMITED = 4429
#: Gate failure that is neither a credential nor a quota problem (e.g. the
#: fail-closed 503 when the shared limiter store is down).
WS_CLOSE_UNAVAILABLE = 4503

#: Roles admitted to chat — identical to ``core.middleware.require_user``.
_CHAT_ROLES = frozenset({"user", "admin", "job", "scoped"})

#: Cap on one inbound query frame — ``ChatRequest``'s own ``max_length``, so
#: an oversized frame is rejected with an error frame (REST parity: never
#: silently truncated).
MAX_QUERY_CHARS = 8000


def _get_security_manager() -> Any:
    from core.middleware.security import get_security_manager

    return get_security_manager()


def _get_chat_service() -> Any:
    from core.chat import chat_service

    return chat_service


def _close_code(status_code: int) -> int:
    """Map the gate's HTTP status to the app-defined close code."""
    if status_code == 401:
        return WS_CLOSE_UNAUTHENTICATED
    if status_code == 403:
        return WS_CLOSE_FORBIDDEN
    if status_code == 429:
        return WS_CLOSE_RATE_LIMITED
    return WS_CLOSE_UNAVAILABLE


async def _enforce(websocket: WebSocket) -> str:
    """Run the REST chat gate on this connection; returns the matched role.

    A ``WebSocket`` is a Starlette ``HTTPConnection`` (headers, client,
    state, url), which is all ``enforce_auth`` reads — so the WebSocket
    surface shares the policy object instead of re-implementing it. Raises
    ``HTTPException`` exactly as the REST dependency would.
    """
    manager = _get_security_manager()
    return await manager.enforce_auth(
        websocket,
        allowed_roles=_CHAT_ROLES,
        limit_per_minute=manager.config.rate_limit_user_per_minute,
    )


def _user_id(websocket: WebSocket) -> str:
    user = getattr(websocket.state, "user", None)
    return getattr(user, "user_id", "anonymous")


@router.websocket("/chat/ws")
async def chat_ws(websocket: WebSocket) -> None:
    """Conversational WebSocket: one authenticated connection, many turns."""
    try:
        await _enforce(websocket)
    except HTTPException as exc:
        # Close BEFORE accept: the handshake is rejected outright, with the
        # gate's generic detail (never the discriminating reason).
        await websocket.close(code=_close_code(exc.status_code), reason=str(exc.detail))
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
            # Re-run the gate per turn: one turn is metered like one REST
            # request (per-identity limit), and a credential revoked or
            # expired mid-session stops being honoured at the next turn.
            try:
                await _enforce(websocket)
            except HTTPException as exc:
                if exc.status_code == 429:
                    retry_after = (exc.headers or {}).get("Retry-After")
                    frame: dict[str, Any] = {"type": "error", "detail": exc.detail}
                    if retry_after is not None:
                        frame["retry_after"] = retry_after
                    await websocket.send_json(frame)
                    continue
                await websocket.close(
                    code=_close_code(exc.status_code), reason=str(exc.detail)
                )
                return
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
        logger.debug("chat_ws_disconnected", extra={"user_id": _user_id(websocket)})


__all__ = ["router"]
