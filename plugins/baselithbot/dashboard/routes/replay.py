"""Task replay routes (list recorded runs + per-step playback)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Query

if TYPE_CHECKING:
    from ...plugin import BaselithbotPlugin

REPLAY_RETENTION_DAYS = 14


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
        status_counts = Counter(str(run.get("status") or "unknown") for run in runs)
        latest_started_ts = max(
            (
                float(run["started_at"])
                for run in runs
                if run.get("started_at") is not None
            ),
            default=None,
        )
        latest_completed_ts = max(
            (
                float(run["completed_at"])
                for run in runs
                if run.get("completed_at") is not None
            ),
            default=None,
        )
        return {
            "runs": runs,
            "returned": len(runs),
            "status_counts": dict(status_counts),
            "step_totals": sum(int(run.get("step_count") or 0) for run in runs),
            "active_runs": status_counts.get("running", 0),
            "latest_started_ts": latest_started_ts,
            "latest_completed_ts": latest_completed_ts,
            "path": str(Path(plugin._state_dir) / "replay.sqlite"),
            "retention_days": REPLAY_RETENTION_DAYS,
        }

    @router.get("/replay/runs/{run_id}")
    async def get_replay_run(
        run_id: str,
        include_screenshots: bool = Query(default=True),
    ) -> dict[str, Any]:
        """Return a single persisted run with all its steps.

        Pass ``include_screenshots=false`` to skip the large base64 blobs on
        the timeline payload. Individual screenshots can then be hydrated via
        :func:`get_replay_run_step_screenshot` as the user scrubs through the
        steps, keeping the initial page load fast.
        """
        run = plugin.replay.get_run(run_id, include_screenshots=include_screenshots)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return {"run": run}

    @router.get("/replay/runs/{run_id}/steps/{step_index}/screenshot")
    async def get_replay_run_step_screenshot(
        run_id: str, step_index: int
    ) -> dict[str, Any]:
        """Return a single step's screenshot on demand (decrypted base64)."""
        b64 = await plugin.replay.aget_run_step_screenshot(run_id, step_index)
        if b64 is None:
            raise HTTPException(status_code=404, detail="screenshot not found")
        return {"screenshot_b64": b64}


__all__ = ["register_replay_routes"]
