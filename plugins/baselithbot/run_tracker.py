"""Live run-task tracker for the Baselithbot dashboard."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Literal

from pydantic import BaseModel, Field


RunTaskStatus = Literal["running", "completed", "failed"]


class RunTaskState(BaseModel):
    """Mutable state exposed to the dashboard for a single run task."""

    run_id: str
    status: RunTaskStatus
    goal: str
    start_url: str | None = None
    max_steps: int
    extract_fields: list[str] = Field(default_factory=list)
    started_at: float
    completed_at: float | None = None
    steps_taken: int = 0
    current_url: str = ""
    final_url: str = ""
    last_action: str | None = None
    last_reasoning: str | None = None
    extracted_data: dict[str, Any] = Field(default_factory=dict)
    history: list[str] = Field(default_factory=list)
    error: str | None = None
    last_screenshot_b64: str | None = None


class RunTaskTracker:
    """Keep a bounded history of recent run tasks for the dashboard."""

    def __init__(self, max_runs: int = 12) -> None:
        self._max_runs = max_runs
        self._lock = threading.RLock()
        self._runs: OrderedDict[str, RunTaskState] = OrderedDict()

    def start(
        self,
        *,
        run_id: str,
        goal: str,
        start_url: str | None,
        max_steps: int,
        extract_fields: list[str],
    ) -> RunTaskState:
        with self._lock:
            state = RunTaskState(
                run_id=run_id,
                status="running",
                goal=goal,
                start_url=start_url,
                max_steps=max_steps,
                extract_fields=list(extract_fields),
                started_at=time.time(),
            )
            self._runs[run_id] = state
            self._runs.move_to_end(run_id)
            self._trim()
            return state.model_copy(deep=True)

    def step(
        self,
        run_id: str,
        *,
        steps_taken: int,
        current_url: str,
        action: str,
        reasoning: str,
        history: list[str],
        extracted_data: dict[str, Any],
        last_screenshot_b64: str | None,
    ) -> RunTaskState | None:
        with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                return None
            state.steps_taken = steps_taken
            state.current_url = current_url
            state.last_action = action
            state.last_reasoning = reasoning
            state.history = list(history)
            state.extracted_data = dict(extracted_data)
            state.last_screenshot_b64 = last_screenshot_b64
            self._runs.move_to_end(run_id)
            return state.model_copy(deep=True)

    def finish(
        self,
        run_id: str,
        *,
        success: bool,
        final_url: str,
        steps_taken: int,
        extracted_data: dict[str, Any],
        history: list[str],
        error: str | None,
        last_screenshot_b64: str | None,
    ) -> RunTaskState | None:
        with self._lock:
            state = self._runs.get(run_id)
            if state is None:
                return None
            state.status = "completed" if success else "failed"
            state.completed_at = time.time()
            state.steps_taken = steps_taken
            state.current_url = final_url
            state.final_url = final_url
            state.extracted_data = dict(extracted_data)
            state.history = list(history)
            state.error = error
            state.last_screenshot_b64 = last_screenshot_b64
            self._runs.move_to_end(run_id)
            return state.model_copy(deep=True)

    def get(self, run_id: str) -> RunTaskState | None:
        with self._lock:
            state = self._runs.get(run_id)
            return state.model_copy(deep=True) if state is not None else None

    def latest(self) -> RunTaskState | None:
        with self._lock:
            if not self._runs:
                return None
            _, state = next(reversed(self._runs.items()))
            return state.model_copy(deep=True)

    def recent(self, limit: int = 8) -> list[RunTaskState]:
        with self._lock:
            items = list(self._runs.values())[-max(0, limit) :]
            return [item.model_copy(deep=True) for item in reversed(items)]

    def _trim(self) -> None:
        while len(self._runs) > self._max_runs:
            self._runs.popitem(last=False)


__all__ = ["RunTaskState", "RunTaskStatus", "RunTaskTracker"]
