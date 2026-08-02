"""
Default checkpoint-store factory.

Resolves the process-wide checkpoint store from
:class:`~core.config.orchestration.OrchestrationConfig`
(``ORCHESTRATOR_CHECKPOINT_ENABLED`` / ``ORCHESTRATOR_CHECKPOINT_BACKEND``) so
the chat service, the approvals API and any other transport share one store —
a decision recorded through the API is visible to the orchestrator that
resumes the run.

Disabled (the default) resolves to ``None``: the orchestrator runs without
checkpointing, exactly as before.
"""

from __future__ import annotations

import threading

from core.observability.logging import get_logger
from core.orchestration.checkpoint import CheckpointStore, InMemoryCheckpointStore

logger = get_logger(__name__)

_store: CheckpointStore | None = None
_resolved = False
_initialized = False
_lock = threading.Lock()


def _resolve_backend() -> str:
    from core.config.orchestration import get_orchestration_config

    backend = get_orchestration_config().checkpoint_backend.strip().lower()
    if backend == "auto":
        try:
            from core.config.storage import get_storage_config

            return "postgres" if get_storage_config().postgres_enabled else "memory"
        except Exception:  # pragma: no cover - storage config unavailable
            return "memory"
    return backend


def get_default_checkpoint_store() -> CheckpointStore | None:
    """Return the shared checkpoint store, or None when checkpointing is off.

    The store is resolved once per process. The Postgres backend needs its
    schema created before first use — call
    :func:`initialize_default_checkpoint_store` from an async startup hook
    (the app lifespan does this).
    """
    global _store, _resolved
    with _lock:
        if _resolved:
            return _store
        from core.config.orchestration import get_orchestration_config

        if not get_orchestration_config().checkpoint_enabled:
            _resolved = True
            return None
        backend = _resolve_backend()
        if backend == "postgres":
            from core.orchestration.checkpoint_postgres import PostgresCheckpointStore

            _store = PostgresCheckpointStore()
        elif backend == "memory":
            _store = InMemoryCheckpointStore()
        else:
            logger.warning(
                "unknown_checkpoint_backend '%s', falling back to memory", backend
            )
            _store = InMemoryCheckpointStore()
        logger.info("checkpoint_store_resolved backend=%s", backend)
        _resolved = True
        return _store


async def initialize_default_checkpoint_store() -> CheckpointStore | None:
    """Resolve the store and run its async initialization (idempotent DDL)."""
    global _initialized
    store = get_default_checkpoint_store()
    if store is None or _initialized:
        return store
    initialize = getattr(store, "initialize", None)
    if callable(initialize):
        await initialize()
    _initialized = True
    return store


def reset_default_checkpoint_store() -> None:
    """Reset the cached resolution (tests / config reloads)."""
    global _store, _resolved, _initialized
    with _lock:
        _store = None
        _resolved = False
        _initialized = False


__all__ = [
    "get_default_checkpoint_store",
    "initialize_default_checkpoint_store",
    "reset_default_checkpoint_store",
]
