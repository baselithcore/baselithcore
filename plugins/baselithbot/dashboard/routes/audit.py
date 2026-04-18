"""Audit log tail route for the dashboard UI."""

from __future__ import annotations

import json
from collections import deque
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Query

if TYPE_CHECKING:
    from ...plugin import BaselithbotPlugin


_MAX_TAIL = 2000


def register_audit_routes(
    router: APIRouter,
    plugin: "BaselithbotPlugin",
) -> None:
    @router.get("/audit-log")
    async def tail_audit_log(
        limit: int = Query(default=200, ge=1, le=_MAX_TAIL),
        action: str | None = Query(default=None, max_length=64),
    ) -> dict[str, Any]:
        """Return the last ``limit`` audit-log entries from the JSONL file.

        Filters by ``action`` substring when provided. Returns an empty list
        plus a ``configured`` flag when no audit-log path is set in
        ComputerUseConfig — the UI uses that to render an empty-state.
        """
        cfg = plugin.effective_computer_use_config()
        path_str = cfg.audit_log_path
        if not path_str:
            return {"configured": False, "path": None, "entries": []}

        from pathlib import Path

        path = Path(path_str)
        if not path.exists():
            return {"configured": True, "path": str(path), "entries": []}

        try:
            tail: deque[str] = deque(maxlen=limit)
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    tail.append(line)
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"audit log read failed: {exc}"
            ) from exc

        entries: list[dict[str, Any]] = []
        needle = action.strip().lower() if action else None
        for line in tail:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                entry = {"raw": line.rstrip()}
            if needle:
                act = str(entry.get("action", "")).lower()
                if needle not in act:
                    continue
            entries.append(entry)
        return {
            "configured": True,
            "path": str(path),
            "entries": entries,
            "returned": len(entries),
            "tail_window": limit,
        }


__all__ = ["register_audit_routes"]
