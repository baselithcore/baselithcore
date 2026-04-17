"""Read-only run-task routes for the dashboard."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException

if TYPE_CHECKING:
    from ...plugin import BaselithbotPlugin


def register_run_task_routes(
    router: APIRouter,
    plugin: "BaselithbotPlugin",
) -> None:
    @router.get("/run-task/latest")
    async def run_task_latest() -> dict[str, Any]:
        state = plugin.run_tracker.latest()
        return {"run": state.model_dump() if state is not None else None}

    @router.get("/run-task/recent")
    async def run_task_recent(limit: int = 8) -> dict[str, Any]:
        runs = plugin.run_tracker.recent(limit=limit)
        return {"runs": [run.model_dump() for run in runs]}

    @router.get("/run-task/{run_id}")
    async def run_task_detail(run_id: str) -> dict[str, Any]:
        state = plugin.run_tracker.get(run_id)
        if state is None:
            raise HTTPException(status_code=404, detail="run task not found")
        return {"run": state.model_dump()}


__all__ = ["register_run_task_routes"]
