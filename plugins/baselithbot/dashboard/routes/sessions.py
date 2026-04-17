"""Session CRUD + send routes for the Baselithbot dashboard."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ...policies import RateLimiter
from ...sessions.manager import SessionMessage
from ..bus import _BUS
from ..schemas import SessionCreateRequest, SessionSendRequest
from ..security import enforce
from ..session_driver import drive_session_reply

if TYPE_CHECKING:
    from ...plugin import BaselithbotPlugin


def register_session_routes(
    router: APIRouter,
    plugin: "BaselithbotPlugin",
    *,
    guard: Any,
    session_rate_limit: RateLimiter,
    delete_rate_limit: RateLimiter,
) -> None:
    @router.get("/sessions")
    async def list_sessions() -> dict[str, Any]:
        return {
            "sessions": [s.model_dump() for s in plugin.sessions.list()],
        }

    @router.post("/sessions", dependencies=[Depends(guard)])
    async def create_session(
        req: SessionCreateRequest, request: Request
    ) -> dict[str, Any]:
        enforce(session_rate_limit, request, "session_create")
        session = plugin.sessions.create(title=req.title, primary=req.primary)
        _BUS.publish("session.created", session.model_dump())
        return session.model_dump()

    @router.get("/sessions/{sid}/history")
    async def session_history(sid: str, limit: int = 100) -> dict[str, Any]:
        if plugin.sessions.get(sid) is None:
            raise HTTPException(status_code=404, detail="session not found")
        return {
            "session_id": sid,
            "messages": [m.model_dump() for m in plugin.sessions.history(sid, limit)],
        }

    @router.post("/sessions/{sid}/send", dependencies=[Depends(guard)])
    async def session_send(
        sid: str, req: SessionSendRequest, request: Request
    ) -> dict[str, Any]:
        enforce(session_rate_limit, request, "session_send")
        if plugin.sessions.get(sid) is None:
            raise HTTPException(status_code=404, detail="session not found")
        try:
            msg = plugin.sessions.send(
                sid,
                SessionMessage(
                    role=req.role, content=req.content, metadata=req.metadata
                ),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        _BUS.publish("session.message", {"session_id": sid, **msg.model_dump()})

        reply: dict[str, Any] = {"kind": "none"}
        if req.role == "user":
            reply = await drive_session_reply(plugin, sid, req.content)

        return {**msg.model_dump(), "reply": reply}

    @router.post("/sessions/{sid}/reset", dependencies=[Depends(guard)])
    async def session_reset(sid: str) -> dict[str, Any]:
        if plugin.sessions.get(sid) is None:
            raise HTTPException(status_code=404, detail="session not found")
        plugin.sessions.reset(sid)
        _BUS.publish("session.reset", {"session_id": sid})
        return {"status": "ok", "session_id": sid}

    @router.delete("/sessions/{sid}", dependencies=[Depends(guard)])
    async def session_delete(sid: str, request: Request) -> dict[str, Any]:
        enforce(delete_rate_limit, request, "session_delete")
        existed = plugin.sessions.delete(sid)
        if not existed:
            raise HTTPException(status_code=404, detail="session not found")
        _BUS.publish("session.deleted", {"session_id": sid})
        return {"status": "deleted", "session_id": sid}


__all__ = ["register_session_routes"]
