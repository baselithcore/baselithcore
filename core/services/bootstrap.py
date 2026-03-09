"""
Bootstrap Service.

Handles initial application state setup, and indexing operations.
"""

from __future__ import annotations

import asyncio
from core.observability.logging import get_logger
from contextlib import suppress
from pathlib import Path
from typing import Any, Optional

from core.services.indexing import get_indexing_service
from core.config import get_app_config

logger = get_logger(__name__)


class IndexBootstrapper:
    """
    Orchestrates the initial indexing and state restoration for the application.

    Ensures that the search indices and graph databases are populated on
    startup or when configuration changes, supporting both full and
    incremental modes.
    """

    def __init__(
        self,
        enabled: bool,
        sentinel_path: Path,
        state_path: Path,
    ) -> None:
        """
        Initialize the bootstrapper.

        Args:
            enabled: Global toggle for bootstrap operations.
            sentinel_path: File path marking successful full bootstrap.
            state_path: File path for persisting indexing metadata.
        """
        self.enabled = enabled
        self._sentinel = sentinel_path
        self._state_path = state_path
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._current_mode: Optional[str] = None
        self._last_error: Optional[str] = None
        self._last_completed_mode: Optional[str] = None

    def is_bootstrapped(self) -> bool:
        """
        Check if a full bootstrap has ever been completed.

        Returns:
            bool: True if the sentinel file exists.
        """
        return self._sentinel.exists()

    def is_running(self) -> bool:
        """
        Check if a bootstrap background task is currently active.

        Returns:
            bool: True if a task exists and has not finished.
        """
        task = self._task
        return task is not None and not task.done()

    def status(self) -> dict[str, Any]:
        """
        Retrieve current operational metrics and state.

        Returns:
            dict[str, Any]: Mapping of status indicators (running, mode, etc.).
        """
        return {
            "bootstrapped": self.is_bootstrapped(),
            "running": self.is_running(),
            "mode": self._current_mode,
            "error": self._last_error,
            "last_completed": self._last_completed_mode,
        }

    def has_pending_changes(self) -> bool:
        """Check if indexing is required.

        Now that indexing is optimized (smart header scan), we can be more aggressive
        about checking. We largely defer to the actual indexer to skip unchanged files.
        """
        if not self.is_bootstrapped():
            return True

        if not self._state_path.exists():
            return True

        # If we have state, let the fast indexer run and check timestamps
        return True

    async def schedule(
        self,
        *,
        force_full: bool = False,
        mode: Optional[str] = None,
        skip_if_no_changes: bool = True,
    ) -> bool:
        """
        Queue a bootstrap operation for background execution.

        Args:
            force_full: If True, ignore existing sentinel and run full indexing.
            mode: Explicit mode ('full' or 'incremental').
            skip_if_no_changes: If True, exit early if no delta is detected.

        Returns:
            bool: True if the task was successfully scheduled.
        """
        if not self.enabled:
            logger.info("Index bootstrap disabilitato via config, skip schedule.")
            return False

        async with self._lock:
            if self.is_running():
                return False

            # Quick change detection optimization
            if skip_if_no_changes and not force_full and self.is_bootstrapped():
                if not self.has_pending_changes():
                    logger.info(
                        "[index] No pending changes detected, skipping bootstrap (startup optimization)."
                    )
                    return False

            resolved_mode = mode
            if resolved_mode is None:
                resolved_mode = (
                    "full"
                    if force_full or not self.is_bootstrapped()
                    else "incremental"
                )

            self._current_mode = resolved_mode
            self._task = asyncio.create_task(self._run(resolved_mode))
            self._last_error = None
            return True

    async def _run(self, mode: str) -> None:
        """
        Internal execution logic for the bootstrap task.

        Args:
            mode: The type of operation to perform ('full' or 'incremental').
        """
        incremental = mode != "full"
        logger.info("[index] bootstrap task started (%s)", mode)
        try:
            # Assicura che i constraint del grafo siano attivi
            from core.graph import graph_db

            if graph_db.is_enabled():
                graph_db.create_constraints()

            await get_indexing_service().index_documents(incremental=incremental)
            if mode == "full":
                self._mark_bootstrapped()
            self._last_completed_mode = mode
            logger.info("[index] bootstrap task completed (%s)", mode)
        except Exception as exc:  # pragma: no cover - errors logged
            self._last_error = str(exc)
            logger.exception("[index] bootstrap task failed (%s): %s", mode, exc)
        finally:
            self._current_mode = None
            async with self._lock:
                self._task = None

    def _mark_bootstrapped(self) -> None:
        """Create a sentinel file to prevent redundant full bootstrap runs."""
        self._sentinel.parent.mkdir(parents=True, exist_ok=True)
        self._sentinel.write_text("ok")

    def register_manual_completion(self, mode: str) -> None:
        """
        Mark the bootstrap as finished from an external trigger.

        Used by CLI or admin tools that perform indexing outside
        the standard service lifecycle.

        Args:
            mode: The completion level ('full' or 'incremental').
        """
        self._last_completed_mode = mode
        if mode == "full":
            self._mark_bootstrapped()
        self._last_error = None

    async def shutdown(self) -> None:
        """
        Gracefully terminate active bootstrap tasks and cleanup locks.
        """
        async with self._lock:
            task = self._task
            self._task = None
        if task is None:
            return
        task.cancel()
        with suppress(Exception, asyncio.CancelledError):
            await task


def create_bootstrapper() -> IndexBootstrapper:
    """Factory to create bootstrapper from current config."""
    app_config = get_app_config()

    # Use data directory for state files to allow read-only documents mount
    data_root = Path("data")
    data_root.mkdir(exist_ok=True)

    return IndexBootstrapper(
        enabled=app_config.index_bootstrap_enabled,
        sentinel_path=data_root / ".bootstrap_sentinel",
        state_path=data_root / ".index_state.json",
    )


# Global instance
bootstrapper = create_bootstrapper()


async def ensure_startup_bootstrap() -> None:
    """
    Convenience wrapper to trigger bootstrap on application initialization.

    Should be called during the FastAPI startup event or CLI entry point.
    """
    await bootstrapper.schedule()


__all__ = ["bootstrapper", "ensure_startup_bootstrap"]
