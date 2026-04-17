"""Failover policy for model providers.

Wraps an ordered list of provider configurations and yields them in order
until one succeeds. Honors per-provider cool-down windows after failures.
"""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, Field


class ProviderError(RuntimeError):
    """Raised by the wrapped provider call to trigger failover."""


class ProviderConfig(BaseModel):
    name: str
    model: str
    weight: int = Field(default=1, ge=1)
    cooldown_seconds: float = Field(default=30.0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class _ProviderState:
    __slots__ = ("config", "last_failure_at", "failures", "successes")

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self.last_failure_at: float = 0.0
        self.failures: int = 0
        self.successes: int = 0


class FailoverPolicy:
    """Iterate providers in priority order, skipping ones in cooldown."""

    def __init__(self, providers: list[ProviderConfig]) -> None:
        if not providers:
            raise ValueError("FailoverPolicy requires at least one provider")
        self._states = [_ProviderState(p) for p in providers]

    def status(self) -> list[dict[str, Any]]:
        now = time.time()
        return [
            {
                "name": s.config.name,
                "model": s.config.model,
                "in_cooldown": (now - s.last_failure_at) < s.config.cooldown_seconds
                and s.failures > 0,
                "failures": s.failures,
                "successes": s.successes,
                "last_failure_at": s.last_failure_at or None,
            }
            for s in self._states
        ]

    async def call(
        self,
        action: Callable[[ProviderConfig], Awaitable[Any]],
    ) -> Any:
        """Try providers in order until one returns without ``ProviderError``."""
        last_error: Exception | None = None
        now = time.time()
        for state in self._states:
            if (
                state.failures > 0
                and (now - state.last_failure_at) < state.config.cooldown_seconds
            ):
                continue
            try:
                result = await action(state.config)
                state.successes += 1
                state.failures = 0
                return {"provider": state.config.name, "result": result}
            except ProviderError as exc:
                state.failures += 1
                state.last_failure_at = time.time()
                last_error = exc
        raise ProviderError(
            f"all providers failed; last error: {last_error}"
        ) from last_error


__all__ = ["FailoverPolicy", "ProviderConfig", "ProviderError"]
