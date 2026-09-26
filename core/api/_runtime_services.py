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


async def drain_orchestrator() -> None:
    """Let the chat orchestrator finish its background memory writes.

    Runs before the storage pools close: a write still in flight when the
    Postgres/Redis pools go away fails with a closed-pool error and the turn's
    memory is lost. Reads the module global rather than ``get_chat_service()``
    so a process that never served a chat does not build one just to close it.
    """
    try:
        from core.chat import service as chat_service_module

        chat_service = chat_service_module._chat_service
        orchestrator = getattr(chat_service, "_agent", None)
        aclose = getattr(orchestrator, "aclose", None)
        if aclose is not None:
            await aclose()
    except Exception as exc:
        logger.warning("orchestrator_drain_failed: %s", exc)


async def close_shared_clients() -> None:
    """Close the cached LLM services and the vector-store client (idempotent)."""
    try:
        from core.services.llm.runtime import close_llm_services

        await close_llm_services()
    except Exception as exc:
        logger.warning("llm_services_close_failed: %s", exc)
    try:
        from core.services.vectorstore.service import close_vectorstore_service

        await close_vectorstore_service()
    except Exception as exc:
        logger.warning("vectorstore_close_failed: %s", exc)


__all__ = [
    "close_shared_clients",
    "drain_orchestrator",
    "start_runtime_services",
    "stop_runtime_services",
]
