"""FastAPI router for the Baselithbot plugin (minimal V1 surface)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .types import BaselithbotResult, BaselithbotTask

if TYPE_CHECKING:
    from .plugin import BaselithbotPlugin


class RunRequest(BaseModel):
    """Request body for ``POST /api/baselithbot/run``."""

    goal: str = Field(..., min_length=1, max_length=4000)
    start_url: str | None = None
    max_steps: int = Field(default=20, ge=1, le=100)
    extract_fields: list[str] = Field(default_factory=list)


class StatusResponse(BaseModel):
    """Response body for ``GET /api/baselithbot/status``."""

    state: str
    backend_started: bool
    stealth_enabled: bool


def create_router(plugin: "BaselithbotPlugin") -> APIRouter:
    """Create the Baselithbot FastAPI router bound to a plugin instance."""
    router = APIRouter(prefix="/baselithbot", tags=["Baselithbot"])

    @router.post("/run", response_model=BaselithbotResult)
    async def run(req: RunRequest) -> BaselithbotResult:
        agent = await plugin.get_or_start_agent()
        task = BaselithbotTask(
            goal=req.goal,
            start_url=req.start_url,
            max_steps=req.max_steps,
            extract_fields=req.extract_fields,
        )
        try:
            return await agent.execute(task)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.get("/status", response_model=StatusResponse)
    async def status() -> StatusResponse:
        agent = plugin.agent
        if agent is None:
            return StatusResponse(
                state="uninitialized",
                backend_started=False,
                stealth_enabled=False,
            )
        return StatusResponse(
            state=agent.state.value,
            backend_started=agent._backend is not None,
            stealth_enabled=agent.stealth_config.enabled,
        )

    return router


__all__ = ["create_router", "RunRequest", "StatusResponse"]
