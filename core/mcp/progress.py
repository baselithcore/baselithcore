"""Progress reporting from inside a tool or resource handler.

The progress token belongs to the *request*, not to the handler's signature, so
it travels in a context variable: a handler calls :func:`report_progress` and
the dispatcher routes the notification to the right client without every tool
having to accept and thread a reporter argument.

Progress is opt-in per the spec — a request that carried no
``_meta.progressToken`` gets no notifications, and the call is a no-op.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from core.observability.logging import get_logger

logger = get_logger(__name__)

Sender = Callable[[dict[str, Any]], Awaitable[None]]

# (progress token, outbound sender) for the request currently being served.
progress_context: ContextVar[tuple[Any, Sender] | None] = ContextVar(
    "mcp_progress_context", default=None
)


async def report_progress(
    progress: float, total: float | None = None, message: str | None = None
) -> None:
    """Emit a ``notifications/progress`` for the request being served.

    Args:
        progress: Work done so far; must increase across calls.
        total: Optional expected total, enabling a percentage.
        message: Optional human-readable status.

    No-op outside a request, or when the client did not request progress.
    """
    context = progress_context.get()
    if context is None:
        return
    token, send = context

    params: dict[str, Any] = {"progressToken": token, "progress": progress}
    if total is not None:
        params["total"] = total
    if message is not None:
        params["message"] = message

    try:
        await send(
            {"jsonrpc": "2.0", "method": "notifications/progress", "params": params}
        )
    except Exception as exc:
        # Progress is advisory: a dead outbound channel must not fail the work.
        logger.debug("mcp_progress_send_failed", error=str(exc))


__all__ = ["progress_context", "report_progress"]
