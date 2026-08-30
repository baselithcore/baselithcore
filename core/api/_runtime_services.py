"""Optional runtime services started/stopped by the app lifespan.

Extracted from :mod:`core.api.lifespan` for the module size cap. Each service
is opt-in via env and fail-open at startup: a service that cannot start logs
a warning and the app boots without it (degraded, never down).

Current services:

* **Run-events bridge** (``BASELITH_RUN_EVENTS_BRIDGE=redis``) — cross-replica
  fan-out of run events over Redis pub/sub, so any replica can serve any
  run's SSE feed.
* **Prompt sync** (``BASELITH_PROMPT_SYNC=postgres``) — durable prompt
  catalog: write-through Postgres backend + per-replica refresh loop, so
  runtime label promotion reaches every replica.
"""

from __future__ import annotations

import os
from typing import Any

from core.observability.logging import get_logger

logger = get_logger(__name__)


async def start_runtime_services(app: Any) -> None:
    """Start the opt-in runtime services; failures degrade, never abort."""
    if os.environ.get("BASELITH_RUN_EVENTS_BRIDGE", "").strip().lower() == "redis":
        try:
            from core.orchestration.run_events_bridge import RedisRunEventsBridge

            app.state.run_events_bridge = RedisRunEventsBridge()
            await app.state.run_events_bridge.start()
        except Exception as exc:
            logger.warning("run_events_bridge_start_failed: %s", exc)

    try:
        from core.prompts.sync import start_prompt_sync_from_env

        app.state.prompt_sync = await start_prompt_sync_from_env()
    except Exception as exc:
        logger.warning("prompt_sync_start_failed: %s", exc)


async def stop_runtime_services(app: Any) -> None:
    """Stop whatever runtime services were started (idempotent)."""
    bridge = getattr(app.state, "run_events_bridge", None)
    if bridge is not None:
        try:
            await bridge.stop()
        except Exception as exc:
            logger.warning("run_events_bridge_stop_failed: %s", exc)

    prompt_sync = getattr(app.state, "prompt_sync", None)
    if prompt_sync is not None:
        try:
            await prompt_sync.stop()
        except Exception as exc:
            logger.warning("prompt_sync_stop_failed: %s", exc)


__all__ = ["start_runtime_services", "stop_runtime_services"]
