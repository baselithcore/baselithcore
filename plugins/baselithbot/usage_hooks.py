"""Auto-emission helpers wiring ``UsageLedger`` into hot paths.

Provides a context manager + decorator that capture latency and surface
token / cost numbers attached by the wrapped callable through the ``usage``
keyword argument of the returned dict.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from functools import wraps
from typing import Any, AsyncIterator, Awaitable, Callable

from .usage import UsageEvent, UsageLedger


@asynccontextmanager
async def measure_usage(
    ledger: UsageLedger,
    *,
    session_id: str | None = None,
    agent_id: str | None = None,
    channel: str | None = None,
    model: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Capture wall-clock latency and emit a ``UsageEvent`` on exit.

    Yields a mutable dict so the caller can attach ``prompt_tokens``,
    ``completion_tokens``, ``cost_usd`` before the context closes.
    """
    payload: dict[str, Any] = {}
    started = time.time()
    try:
        yield payload
    finally:
        ledger.record(
            UsageEvent(
                session_id=session_id,
                agent_id=agent_id,
                channel=channel,
                model=model,
                prompt_tokens=int(payload.get("prompt_tokens", 0) or 0),
                completion_tokens=int(payload.get("completion_tokens", 0) or 0),
                cost_usd=float(payload.get("cost_usd", 0.0) or 0.0),
                latency_ms=(time.time() - started) * 1000.0,
                metadata=dict(payload.get("metadata", {}) or {}),
            )
        )


def emit_usage_on_call(
    ledger: UsageLedger,
    *,
    agent_id: str | None = None,
    channel: str | None = None,
) -> Callable[[Callable[..., Awaitable[dict[str, Any]]]], Callable[..., Awaitable[dict[str, Any]]]]:
    """Decorator: emit a ``UsageEvent`` after each successful coroutine call.

    The wrapped coroutine is expected to return a dict; if it includes a
    ``"usage"`` key with token / cost fields they are recorded verbatim.
    """

    def _decorate(
        fn: Callable[..., Awaitable[dict[str, Any]]],
    ) -> Callable[..., Awaitable[dict[str, Any]]]:
        @wraps(fn)
        async def _wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            started = time.time()
            try:
                result = await fn(*args, **kwargs)
            finally:
                pass
            usage_block: dict[str, Any] = (
                result.get("usage", {}) if isinstance(result, dict) else {}
            )
            ledger.record(
                UsageEvent(
                    agent_id=agent_id,
                    channel=channel,
                    model=usage_block.get("model"),
                    session_id=usage_block.get("session_id"),
                    prompt_tokens=int(usage_block.get("prompt_tokens", 0) or 0),
                    completion_tokens=int(usage_block.get("completion_tokens", 0) or 0),
                    cost_usd=float(usage_block.get("cost_usd", 0.0) or 0.0),
                    latency_ms=(time.time() - started) * 1000.0,
                )
            )
            return result

        return _wrapper

    return _decorate


__all__ = ["measure_usage", "emit_usage_on_call"]
