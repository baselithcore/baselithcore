"""
Graceful shutdown handler.

Provides signal handling for clean application shutdown.
"""

import asyncio
from core.observability.logging import get_logger
import signal
from typing import Callable, List, Optional

logger = get_logger(__name__)


class GracefulShutdown:
    """
    Handles graceful shutdown of the application.

    Usage:
        shutdown = GracefulShutdown()
        shutdown.register(cleanup_database)
        shutdown.register(close_connections)
        shutdown.install_handlers()

        # In your main:
        await shutdown.wait_for_shutdown()
    """

    def __init__(self, timeout: int = 30):
        """
        Initialize shutdown handler.

        Args:
            timeout: Maximum seconds to wait for cleanup (default 30s)
        """
        self._timeout = timeout
        self._callbacks: List[Callable] = []
        self._shutdown_event: Optional[asyncio.Event] = None
        self._is_shutting_down = False

    def register(self, callback: Callable) -> None:
        """
        Register a cleanup callback.

        Callbacks are called in reverse order (LIFO).

        Args:
            callback: Sync or async function to call during shutdown
        """
        self._callbacks.append(callback)
        logger.debug(f"Registered shutdown callback: {callback.__name__}")

    def install_handlers(self) -> None:
        """Install signal handlers for SIGTERM and SIGINT."""
        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, self._handle_signal, sig)
                logger.info(f"Installed signal handler for {sig.name}")
        except RuntimeError:
            logger.warning("Could not install signal handlers: No running event loop")

    def _handle_signal(self, sig: signal.Signals) -> None:
        """Handle shutdown signal."""
        if self._is_shutting_down:
            logger.warning("Forced shutdown - already shutting down")
            return

        logger.info(f"Received {sig.name}, initiating graceful shutdown...")
        self._is_shutting_down = True

        if self._shutdown_event:
            self._shutdown_event.set()

    async def wait_for_shutdown(self) -> None:
        """Wait for shutdown signal."""
        self._shutdown_event = asyncio.Event()
        await self._shutdown_event.wait()
        await self._execute_callbacks()

    async def _execute_callbacks(self) -> None:
        """Execute all registered callbacks."""
        logger.info(f"Executing {len(self._callbacks)} shutdown callbacks...")

        # Execute in reverse order (LIFO)
        for callback in reversed(self._callbacks):
            try:
                logger.debug(f"Executing: {callback.__name__}")
                if asyncio.iscoroutinefunction(callback):
                    await asyncio.wait_for(
                        callback(), timeout=self._timeout / len(self._callbacks)
                    )
                else:
                    callback()
            except asyncio.TimeoutError:
                logger.error(f"Callback {callback.__name__} timed out")
            except Exception as e:
                logger.error(f"Callback {callback.__name__} failed: {e}")

        logger.info("Graceful shutdown complete")

    @property
    def is_shutting_down(self) -> bool:
        """Check if shutdown is in progress."""
        return self._is_shutting_down


# Global instance
_shutdown_handler: Optional[GracefulShutdown] = None


def get_shutdown_handler(timeout: int = 30) -> GracefulShutdown:
    """Get or create global shutdown handler."""
    global _shutdown_handler
    if _shutdown_handler is None:
        _shutdown_handler = GracefulShutdown(timeout=timeout)
    return _shutdown_handler
