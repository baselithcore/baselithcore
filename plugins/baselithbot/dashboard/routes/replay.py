"""Task replay routes (list recorded runs + per-step playback)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Query

if TYPE_CHECKING:
    from ...plugin import BaselithbotPlugin


def register_replay_routes(
    router: APIRouter,
    plugin: "BaselithbotPlugin",
) -> None:
    @router.get("/replay/runs")
    async def list_replay_runs(
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        """Return the most recent persisted runs + their step count."""
        runs = plugin.replay.list_runs(limit=limit)
        return {"runs": runs, "returned": len(runs)}

    @router.get("/replay/runs/{run_id}")
    async def get_replay_run(run_id: str) -> dict[str, Any]:
        """Return a single persisted run with all its steps and screenshots."""
        run = plugin.replay.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return {"run": run}


__all__ = ["register_replay_routes"]
