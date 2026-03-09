"""
Tests for core.resilience.shutdown module.
"""

import pytest

from core.resilience.shutdown import (
    GracefulShutdown,
    get_shutdown_handler,
)


class TestGracefulShutdownInit:
    """Tests for GracefulShutdown initialization."""

    def test_default_init(self):
        """Test default initialization."""
        shutdown = GracefulShutdown()

        assert shutdown._timeout == 30
        assert shutdown._callbacks == []
        assert shutdown._is_shutting_down is False

    def test_custom_timeout(self):
        """Test custom timeout."""
        shutdown = GracefulShutdown(timeout=60)

        assert shutdown._timeout == 60


class TestGracefulShutdownCallbacks:
    """Tests for callback registration."""

    def test_register_callback(self):
        """Test callback registration."""
        shutdown = GracefulShutdown()

        def cleanup():
            pass

        shutdown.register(cleanup)

        assert len(shutdown._callbacks) == 1
        assert shutdown._callbacks[0] == cleanup

    def test_register_multiple(self):
        """Test multiple callback registration."""
        shutdown = GracefulShutdown()

        shutdown.register(lambda: None)
        shutdown.register(lambda: None)
        shutdown.register(lambda: None)

        assert len(shutdown._callbacks) == 3


class TestGracefulShutdownExecution:
    """Tests for callback execution."""

    @pytest.mark.asyncio
    async def test_execute_sync_callback(self):
        """Test sync callback execution."""
        shutdown = GracefulShutdown()
        called = []

        def sync_cleanup():
            called.append("sync")

        shutdown.register(sync_cleanup)
        shutdown._is_shutting_down = True

        await shutdown._execute_callbacks()

        assert "sync" in called

    @pytest.mark.asyncio
    async def test_execute_async_callback(self):
        """Test async callback execution."""
        shutdown = GracefulShutdown()
        called = []

        async def async_cleanup():
            called.append("async")

        shutdown.register(async_cleanup)
        shutdown._is_shutting_down = True

        await shutdown._execute_callbacks()

        assert "async" in called

    @pytest.mark.asyncio
    async def test_execute_order_lifo(self):
        """Test callbacks execute in LIFO order."""
        shutdown = GracefulShutdown()
        order = []

        shutdown.register(lambda: order.append(1))
        shutdown.register(lambda: order.append(2))
        shutdown.register(lambda: order.append(3))

        await shutdown._execute_callbacks()

        assert order == [3, 2, 1]

    @pytest.mark.asyncio
    async def test_callback_error_continues(self):
        """Test error in callback doesn't stop others."""
        shutdown = GracefulShutdown()
        called = []

        def fail():
            raise ValueError("error")

        def success():
            called.append("success")

        shutdown.register(success)
        shutdown.register(fail)

        await shutdown._execute_callbacks()

        # success should still be called even after fail
        assert "success" in called


class TestGracefulShutdownProperty:
    """Tests for is_shutting_down property."""

    def test_initially_false(self):
        """Test initially not shutting down."""
        shutdown = GracefulShutdown()

        assert shutdown.is_shutting_down is False

    def test_after_signal(self):
        """Test after signal handling."""
        shutdown = GracefulShutdown()
        shutdown._is_shutting_down = True

        assert shutdown.is_shutting_down is True


class TestGetShutdownHandler:
    """Tests for get_shutdown_handler function."""

    def test_returns_instance(self):
        """Test returns GracefulShutdown instance."""
        handler = get_shutdown_handler()

        assert isinstance(handler, GracefulShutdown)

    def test_singleton(self):
        """Test returns same instance."""
        handler1 = get_shutdown_handler()
        handler2 = get_shutdown_handler()

        assert handler1 is handler2
