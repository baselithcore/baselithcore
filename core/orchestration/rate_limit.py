"""Sliding-window rate limiting for side-effecting tool invocations.

The per-run :class:`~core.orchestration.limits.LoopBudget` caps how many
tool calls one request may make in total; nothing caps how *fast* an agent
fires a given side-effecting tool. Fifty emails in ten seconds is within a
generous per-run cap and still an incident. This limiter bounds the burst:
a sliding window keyed ``(tenant, tool)``, enforced only for the autonomy
categories that touch the world (``destructive`` / ``external_side_effect``
by default) so read-heavy loops pay nothing.

Off by default — enable with ``ORCHESTRATOR_TOOL_RATE_LIMIT_ENABLED=true``.
In-process state: each worker enforces its own window (a cross-replica
limiter would need Redis and belongs to the HTTP middleware family).
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Iterable
from threading import Lock

_DEFAULT_LIMITED_CATEGORIES = frozenset({"destructive", "external_side_effect"})


class ToolRateLimitExceededError(RuntimeError):
    """A tool exceeded its sliding-window invocation cap."""

    def __init__(self, tool_name: str, max_calls: int, window_seconds: float):
        self.tool_name = tool_name
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        super().__init__(
            f"tool '{tool_name}' exceeded {max_calls} calls per "
            f"{window_seconds:.0f}s window"
        )


class ToolRateLimiter:
    """In-process sliding-window limiter keyed ``(tenant, tool)``.

    Args:
        max_calls: Calls allowed inside one window per key.
        window_seconds: Window length.
        categories: Autonomy categories the limit applies to; other
            categories always pass.
        now: Clock override (tests).
    """

    def __init__(
        self,
        max_calls: int,
        window_seconds: float,
        *,
        categories: Iterable[str] = _DEFAULT_LIMITED_CATEGORIES,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_calls = max(1, max_calls)
        self._window = window_seconds
        self._categories = frozenset(categories)
        self._now = now
        self._calls: dict[tuple[str, str], deque[float]] = {}
        self._lock = Lock()

    def check(
        self, tool_name: str, category: str, *, tenant_id: str | None = None
    ) -> None:
        """Record one invocation attempt; raise when the window is full.

        Raises:
            ToolRateLimitExceededError: The ``(tenant, tool)`` key has
                already used its ``max_calls`` inside the current window.
        """
        if category not in self._categories:
            return
        key = (tenant_id or "default", tool_name)
        current = self._now()
        with self._lock:
            window = self._calls.setdefault(key, deque())
            cutoff = current - self._window
            while window and window[0] <= cutoff:
                window.popleft()
            if len(window) >= self._max_calls:
                try:
                    from core.observability.metrics import TOOL_RATE_LIMITED_TOTAL

                    TOOL_RATE_LIMITED_TOTAL.labels(tool_name=tool_name).inc()
                except (
                    Exception
                ):  # pragma: no cover - silent-ok: metrics never block a limit decision
                    pass
                raise ToolRateLimitExceededError(
                    tool_name, self._max_calls, self._window
                )
            window.append(current)


_default_limiter: ToolRateLimiter | None = None
_default_resolved = False


def get_default_tool_rate_limiter() -> ToolRateLimiter | None:
    """Config-resolved process-wide limiter; None while disabled."""
    global _default_limiter, _default_resolved
    if not _default_resolved:
        from core.config.orchestration import get_orchestration_config

        config = get_orchestration_config()
        if config.tool_rate_limit_enabled:
            _default_limiter = ToolRateLimiter(
                config.tool_rate_limit_max_calls,
                config.tool_rate_limit_window_seconds,
            )
        _default_resolved = True
    return _default_limiter


def reset_default_tool_rate_limiter() -> None:
    """Drop the cached resolution (tests / config reloads)."""
    global _default_limiter, _default_resolved
    _default_limiter = None
    _default_resolved = False


__all__ = [
    "ToolRateLimitExceededError",
    "ToolRateLimiter",
    "get_default_tool_rate_limiter",
    "reset_default_tool_rate_limiter",
]
