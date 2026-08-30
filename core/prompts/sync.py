"""Cross-replica prompt persistence: write-through backend + refresh loop.

The registry's in-memory store made runtime label promotion replica-local: a
``promote()`` on one replica never reached the others. The synchronizer
closes that gap without changing the registry's synchronous contract:

* **writes** (:meth:`PromptSynchronizer.push_version` /
  :meth:`~PromptSynchronizer.push_label`) go through a durable backend AND
  the local registry store;
* **reads** stay in-memory (zero per-render I/O);
* a per-replica **refresh loop** periodically imports versions/labels written
  elsewhere, so every replica converges within ``interval_seconds``.

Failure policy is fail-open: a broken refresh logs and retries next tick; a
broken backend write raises (the caller must know a promotion did not
persist). Opt-in via ``BASELITH_PROMPT_SYNC=postgres`` (wired in the app
lifespan); the admin surface is ``plugins/api_routers/prompts.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from typing import Any, Protocol, runtime_checkable

from core.observability.logging import get_logger
from core.prompts.registry import PromptRegistry, get_prompt_registry
from core.prompts.types import PromptNotFoundError, PromptVersion

logger = get_logger(__name__)

DEFAULT_REFRESH_INTERVAL_SECONDS = 30.0


@runtime_checkable
class PromptBackend(Protocol):
    """Durable storage contract behind the synchronizer."""

    async def initialize(self) -> None: ...

    async def upsert_version(self, version: PromptVersion) -> None: ...

    async def set_label(self, name: str, label: str, version: str) -> None: ...

    async def fetch_all(
        self,
    ) -> tuple[list[PromptVersion], dict[tuple[str, str], str]]: ...


class PromptSynchronizer:
    """Write-through + periodic-refresh bridge between registry and backend."""

    def __init__(
        self,
        registry: PromptRegistry | None = None,
        backend: PromptBackend | None = None,
        interval_seconds: float = DEFAULT_REFRESH_INTERVAL_SECONDS,
    ) -> None:
        if backend is None:
            raise ValueError("PromptSynchronizer requires a backend")
        self._registry = registry or get_prompt_registry()
        self._backend = backend
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None

    async def push_version(self, version: PromptVersion) -> None:
        """Persist a prompt version and register it locally."""
        await self._backend.upsert_version(version)
        self._registry.store.put(version)

    async def push_label(self, name: str, label: str, version: str) -> None:
        """Persist a label promotion and apply it locally.

        Raises:
            PromptNotFoundError: When ``name@version`` is not registered.
        """
        if self._registry.store.get(name, version) is None:
            raise PromptNotFoundError(f"{name}@{version} not found")
        await self._backend.set_label(name, label, version)
        self._registry.store.set_label(name, label, version)

    async def refresh(self) -> None:
        """Import the backend's versions and labels into the local registry.

        Fail-open: a backend error logs and returns — the next tick retries.
        """
        try:
            versions, labels = await self._backend.fetch_all()
        except Exception as exc:
            logger.warning("prompt_sync_refresh_failed", extra={"error": str(exc)})
            return
        for version in versions:
            self._registry.store.put(version)
        for (name, label), target in labels.items():
            self._registry.store.set_label(name, label, target)

    async def start(self) -> None:
        """Start the periodic refresh loop (idempotent)."""
        if self._task is not None:
            return
        await self.refresh()
        self._task = asyncio.get_running_loop().create_task(
            self._loop(), name="prompt-sync-refresh"
        )

    async def stop(self) -> None:
        """Stop the refresh loop (idempotent)."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            await self.refresh()


_synchronizer: PromptSynchronizer | None = None


def get_prompt_synchronizer() -> PromptSynchronizer | None:
    """The process-wide synchronizer, or None when prompt sync is off."""
    return _synchronizer


def set_prompt_synchronizer(synchronizer: PromptSynchronizer | None) -> None:
    """Install (or clear) the process-wide synchronizer."""
    global _synchronizer
    _synchronizer = synchronizer


async def start_prompt_sync_from_env() -> PromptSynchronizer | None:
    """Build, start and install the synchronizer per env config.

    ``BASELITH_PROMPT_SYNC=postgres`` selects the Postgres backend;
    ``BASELITH_PROMPT_SYNC_INTERVAL`` overrides the refresh interval.
    Returns None (and installs nothing) when sync is not configured.
    """
    mode = os.environ.get("BASELITH_PROMPT_SYNC", "").strip().lower()
    if mode != "postgres":
        if mode:
            logger.warning("prompt_sync_unknown_mode", extra={"mode": mode})
        return None
    from core.prompts.store_postgres import PostgresPromptBackend

    backend: Any = PostgresPromptBackend()
    await backend.initialize()
    interval = float(
        os.environ.get(
            "BASELITH_PROMPT_SYNC_INTERVAL", str(DEFAULT_REFRESH_INTERVAL_SECONDS)
        )
    )
    synchronizer = PromptSynchronizer(backend=backend, interval_seconds=interval)
    await synchronizer.start()
    set_prompt_synchronizer(synchronizer)
    logger.info("prompt_sync_started", extra={"interval_seconds": interval})
    return synchronizer


__all__ = [
    "DEFAULT_REFRESH_INTERVAL_SECONDS",
    "PromptBackend",
    "PromptSynchronizer",
    "get_prompt_synchronizer",
    "set_prompt_synchronizer",
    "start_prompt_sync_from_env",
]
