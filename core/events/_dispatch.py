"""Handler invocation for the event bus: context binding, timeout, DLQ capture.

Split out of :mod:`core.events.bus` along the "how a handler is called" seam
(file-size cap). Purely mechanical — the bus passes its own timeout, stats and
dead-letter queue, so behaviour is unchanged.
"""

from __future__ import annotations

import asyncio
import functools
from typing import Any

from core.events.types import AsyncHandler, SyncHandler
from core.observability.logging import get_logger

logger = get_logger("core.events.bus")

__all__ = ["call_async_handler", "call_sync_handler"]


async def call_async_handler(
    handler: AsyncHandler,
    data: dict[str, Any],
    event_name: str,
    tenant_id: str,
    user_id: str | None = None,
    *,
    timeout: float,
    stats: Any,
    dlq: Any,
) -> None:
    """Call an async handler with error handling."""
    from core.context import (
        reset_tenant_context,
        reset_user_context,
        set_tenant_context,
        set_user_context,
    )

    token = set_tenant_context(tenant_id)
    user_token = set_user_context(user_id) if user_id else None
    try:
        await asyncio.wait_for(handler(data), timeout=timeout)
        stats.events_handled += 1
    except TimeoutError:
        stats.errors += 1
        handler_name = getattr(handler, "__name__", str(handler))
        logger.error(
            f"Handler '{handler_name}' for '{event_name}' timed out after {timeout}s"
        )
        if dlq:
            dlq.add(event_name, data, "TimeoutError", handler_name)
    except Exception as e:
        stats.errors += 1
        logger.error(f"Error in handler for '{event_name}': {e}")
        if dlq:
            handler_name = getattr(handler, "__name__", str(handler))
            dlq.add(event_name, data, str(e), handler_name)
    finally:
        reset_tenant_context(token)
        if user_token is not None:
            reset_user_context(user_token)


async def call_sync_handler(
    handler: SyncHandler,
    data: dict[str, Any],
    event_name: str,
    tenant_id: str,
    user_id: str | None = None,
    *,
    stats: Any,
    dlq: Any,
) -> None:
    """Call a sync handler in executor with error handling."""

    def sync_wrapper(event_data: dict[str, Any]) -> None:
        from core.context import (
            reset_tenant_context,
            reset_user_context,
            set_tenant_context,
            set_user_context,
        )

        tok = set_tenant_context(tenant_id)
        user_tok = set_user_context(user_id) if user_id else None
        try:
            handler(event_data)
        finally:
            reset_tenant_context(tok)
            if user_tok is not None:
                reset_user_context(user_tok)

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, functools.partial(sync_wrapper, data))
        stats.events_handled += 1
    except Exception as e:
        stats.errors += 1
        logger.error(f"Error in sync handler for '{event_name}': {e}")
        if dlq:
            handler_name = getattr(handler, "__name__", str(handler))
            dlq.add(event_name, data, str(e), handler_name)
