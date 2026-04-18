"""Pydantic models + type aliases for the desktop agent loop."""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field, PrivateAttr

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


class DesktopStep(BaseModel):
    """One Observe -> Plan -> Act iteration."""

    step: int
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    reasoning: str = ""
    status: str = ""
    result_summary: str = ""
    ts: float = Field(default_factory=time.time)
    # Precomputed compact JSON of ``args``. Cached on first access so the
    # history context renderer in ``_decide`` does not re-serialise the same
    # dict on every subsequent agent iteration. ``PrivateAttr`` keeps it off
    # the default serialisation surface used by progress callbacks.
    _args_json_cache: str | None = PrivateAttr(default=None)

    @property
    def args_json(self) -> str:
        cached = self._args_json_cache
        if cached is None:
            cached = json.dumps(self.args, separators=(",", ":"))
            self._args_json_cache = cached
        return cached


class DesktopTaskResult(BaseModel):
    """Terminal outcome of a desktop agent run."""

    success: bool
    steps_taken: int
    goal: str
    history: list[DesktopStep] = Field(default_factory=list)
    final_reasoning: str = ""
    error: str | None = None
    last_screenshot_b64: str | None = None
    tokens_used: int = 0
    model: str | None = None
    provider: str | None = None


__all__ = ["DesktopStep", "DesktopTaskResult", "ProgressCallback"]
