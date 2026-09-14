"""
High-Performance Asynchronous Event Bus.

Facilitates decoupled component communication through a robust pub/sub
mechanism. Supports wildcard subscriptions, handler prioritization,
dead-letter queues (DLQ) for fault tolerance, and comprehensive
statistics tracking for observability.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any, cast

from core.events._dispatch import call_async_handler, call_sync_handler
from core.events.types import (
    Event,
    EventStats,
    Handler,
    SyncHandler,
)
from core.events.validation import (
    DeadLetterQueue,
    EventSchemaRegistry,
    EventValidationError,
)
from core.observability.logging import get_logger

logger = get_logger(__name__)


class EventBus:
    """
    Central hub for event-driven orchestration.

    Manages the registry of event handlers and coordinates asynchronous
    message delivery. Features advanced routing via wildcards (e.g.,
    'agent.*'), execution safety through DLQs, and diagnostic history for
    system-wide traceability.
    """

    def __init__(
        self,
        *,
        max_history: int = 100,
        enable_wildcards: bool = True,
        enable_validation: bool = False,
        enable_dlq: bool = False,
        dlq_max_size: int = 1000,
        handler_timeout: float = 30.0,
        schema_registry: EventSchemaRegistry | None = None,
        dlq: DeadLetterQueue | None = None,
    ) -> None:
        """
        Initialize EventBus.

        Args:
            max_history: Maximum number of events to keep in history
            enable_wildcards: Whether to support wildcard subscriptions
            enable_validation: Whether to validate events against schemas
            enable_dlq: Whether to use dead-letter queue for failed handlers
            dlq_max_size: Maximum size of dead-letter queue (used if dlq not provided)
            schema_registry: Optional injected schema registry
            dlq: Optional injected dead letter queue
        """
        self._handlers: dict[str, list[tuple[int, Handler]]] = defaultdict(list)
        self._wildcard_handlers: dict[str, list[tuple[int, Handler]]] = defaultdict(
            list
        )
        self._handler_cache: dict[str, tuple[Handler, ...]] = {}
        self._enable_wildcards = enable_wildcards
        self._enable_validation = enable_validation
        self._enable_dlq = enable_dlq
        self._handler_timeout = handler_timeout

        self._history: deque[Event] = deque(maxlen=max_history)
        self._max_history = max_history

        self._stats = EventStats()
        self._lock: asyncio.Lock | None = None
        # The loop handlers run on. Bound by the first ``emit`` issued from a
        # running loop; ``emit_sync`` marshals onto it from foreign threads.
        self._loop: asyncio.AbstractEventLoop | None = None
        # Strong refs to fire-and-forget handler tasks so the event loop does
        # not garbage-collect them mid-flight (and silently drop exceptions).
        self._background_tasks: set[asyncio.Task[Any]] = set()

        # Optional components
        self._schema_registry: EventSchemaRegistry | None = None
        if enable_validation:
            self._schema_registry = schema_registry or EventSchemaRegistry()

        self._dlq: DeadLetterQueue | None = None
        if enable_dlq:
            self._dlq = dlq or DeadLetterQueue(max_size=dlq_max_size)

        logger.debug("EventBus initialized")

    def subscribe(
        self,
        event_name: str,
        handler: Handler,
        priority: int = 0,
    ) -> Callable[[], None]:
        """
        Subscribe a handler to an event.

        Args:
            event_name: Event name (supports wildcards like "agent.*")
            handler: Sync or async handler function
            priority: Higher priority handlers run first (default 0)

        Returns:
            Unsubscribe function
        """
        if self._enable_wildcards and "*" in event_name:
            target = self._wildcard_handlers[event_name]
        else:
            target = self._handlers[event_name]

        entry = (priority, handler)
        target.append(entry)
        # Sort by priority (descending)
        target.sort(key=lambda x: -x[0])
        self._invalidate_handler_cache()

        self._stats.handlers_registered += 1
        logger.debug(f"Handler subscribed to '{event_name}' with priority {priority}")

        def unsubscribe() -> None:
            """Unsubscribe the handler from the event."""
            if entry in target:
                target.remove(entry)
                self._invalidate_handler_cache()
                logger.debug(f"Handler unsubscribed from '{event_name}'")

        return unsubscribe

    def on(
        self,
        event_name: str,
        priority: int = 0,
    ) -> Callable[[Handler], Handler]:
        """
        Decorator to subscribe a handler to an event.

        Args:
            event_name: Event name to subscribe to
            priority: Handler priority (higher runs first)

        Returns:
            Decorator function
        """

        def decorator(handler: Handler) -> Handler:
            """
            Inner decorator used to subscribe a handler.

            Args:
                handler: The function to be registered as an event handler.

            Returns:
                The original handler function.
            """
            self.subscribe(event_name, handler, priority)
            return handler

        return decorator

    def _match_wildcard(self, pattern: str, event_name: str) -> bool:
        """Check if event name matches wildcard pattern."""
        if pattern == "*":
            return True
        if pattern.endswith(".*"):
            prefix = pattern[:-2]
            return event_name.startswith(prefix + ".")
        if pattern.startswith("*."):
            suffix = pattern[2:]
            return event_name.endswith("." + suffix)
        return pattern == event_name

    def _get_handlers(self, event_name: str) -> tuple[Handler, ...]:
        """Get all handlers for an event, including wildcards."""
        cached_handlers = self._handler_cache.get(event_name)
        if cached_handlers is not None:
            return cached_handlers

        handlers: list[tuple[int, Handler]] = []

        # Direct handlers
        handlers.extend(self._handlers.get(event_name, []))

        # Wildcard handlers
        if self._enable_wildcards:
            for pattern, pattern_handlers in self._wildcard_handlers.items():
                if self._match_wildcard(pattern, event_name):
                    handlers.extend(pattern_handlers)

        # Sort by priority and return just handlers
        handlers.sort(key=lambda x: -x[0])
        resolved_handlers = tuple(h for _, h in handlers)
        self._handler_cache[event_name] = resolved_handlers
        return resolved_handlers

    def _invalidate_handler_cache(self) -> None:
        """Invalidate cached handler resolutions."""
        self._handler_cache.clear()

    async def emit(
        self,
        event_name: str,
        data: dict[str, Any] | None = None,
        *,
        source: str | None = None,
        correlation_id: str | None = None,
        wait: bool = True,
    ) -> int:
        """
        Emit an event to all subscribers.

        Args:
            event_name: Name of the event
            data: Event payload data
            source: Source component name
            correlation_id: ID for tracing related events
            wait: Whether to wait for all handlers to complete

        Returns:
            Number of handlers invoked
        """
        data = data or {}
        self._bind_loop()

        if self._schema_registry is not None:
            is_valid, error = self._schema_registry.validate(event_name, data)
            if not is_valid:
                raise EventValidationError(
                    f"Event '{event_name}' rejected by its schema: {error}"
                )

        from core.context import get_current_tenant_id, get_current_user_id

        try:
            tenant_id = get_current_tenant_id()
        except Exception:
            # Fallback if somehow not present in error boundary
            tenant_id = "default"
        # Capture the emitting identity so handlers (which may run detached)
        # resolve the same per-user tenant via resolve_plugin_tenant.
        user_id = get_current_user_id()

        event = Event(
            name=event_name,
            data=data,
            source=source,
            correlation_id=correlation_id,
        )

        # Store in history
        if self._lock is None:
            self._lock = asyncio.Lock()

        async with self._lock:
            self._history.append(event)

        self._stats.events_published += 1

        handlers = self._get_handlers(event_name)

        if not handlers:
            logger.debug(f"No handlers for event '{event_name}'")
            return 0

        logger.debug(f"Emitting '{event_name}' to {len(handlers)} handlers")

        # Create tasks for all handlers
        tasks = []
        for handler in handlers:
            if asyncio.iscoroutinefunction(handler):
                tasks.append(
                    call_async_handler(
                        handler,
                        data,
                        event_name,
                        tenant_id,
                        user_id,
                        timeout=self._handler_timeout,
                        stats=self._stats,
                        dlq=self._dlq,
                    )
                )
            else:
                # Handler is sync since iscoroutinefunction returned False
                tasks.append(
                    call_sync_handler(
                        cast(SyncHandler, handler),
                        data,
                        event_name,
                        tenant_id,
                        user_id,
                        stats=self._stats,
                        dlq=self._dlq,
                    )
                )

        if wait:
            await asyncio.gather(*tasks, return_exceptions=True)
        else:
            for task in tasks:
                bg = asyncio.create_task(task)
                self._background_tasks.add(bg)
                bg.add_done_callback(self._background_tasks.discard)

        return len(handlers)

    def emit_sync(
        self,
        event_name: str,
        data: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> int:
        """
        Synchronous emit for non-async contexts.

        Args:
            event_name: Name of the event
            data: Event payload data
            **kwargs: Additional emit arguments

        Returns:
            Number of handlers invoked
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            # Schedule as task if loop is running. Keep a strong reference until
            # completion so the event loop can't GC the task mid-flight.
            task = asyncio.ensure_future(self.emit(event_name, data, **kwargs))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
            return 0  # Can't wait in sync context

        # Called from a thread with no loop of its own (``asyncio.to_thread``
        # workers, sync persistence code). Handlers are written for the
        # application's loop — they hold loop-bound resources such as async
        # Redis pools — so they must run *there*, not on a throwaway loop that
        # ``asyncio.run`` tears down the moment ``emit`` returns. That pattern
        # left pooled connections bound to a closed loop for the rest of the
        # process. The standalone path stays for processes without any loop.
        loop = self._loop
        if loop is not None and loop.is_running() and not loop.is_closed():
            future = asyncio.run_coroutine_threadsafe(
                self.emit(event_name, data, **kwargs), loop
            )
            if not kwargs.get("wait", True):
                return 0
            try:
                return future.result(timeout=self._handler_timeout + 1.0)
            except Exception as exc:
                logger.warning(f"emit_sync of '{event_name}' did not complete: {exc}")
                return 0
        return asyncio.run(self.emit(event_name, data, **kwargs))

    def _bind_loop(self) -> None:
        """Remember the running loop as the one handlers execute on.

        The first live loop wins and stays bound while it is open, so a
        temporary loop (a sync ``emit_sync`` before the app started) never
        displaces the application's.
        """
        if self._loop is not None and not self._loop.is_closed():
            return
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

    def clear_handlers(self, event_name: str | None = None) -> None:
        """
        Clear handlers for an event or all events.

        Args:
            event_name: Specific event to clear, or None for all
        """
        if event_name:
            self._handlers.pop(event_name, None)
            self._wildcard_handlers.pop(event_name, None)
        else:
            self._handlers.clear()
            self._wildcard_handlers.clear()
        self._invalidate_handler_cache()
        logger.debug(f"Cleared handlers for: {event_name or 'all events'}")

    def get_history(
        self,
        event_name: str | None = None,
        limit: int = 10,
    ) -> list[Event]:
        """
        Get recent events from history.

        Args:
            event_name: Filter by event name
            limit: Maximum events to return

        Returns:
            List of recent events
        """
        if limit <= 0:
            return []

        events = list(self._history)
        if event_name:
            events = [e for e in events if e.name == event_name]
        return events[-limit:]

    @property
    def stats(self) -> dict[str, Any]:
        """Get event bus statistics."""
        return self._stats.to_dict()

    @property
    def dead_letter_queue(self) -> DeadLetterQueue | None:
        """Get the dead letter queue (if enabled)."""
        return self._dlq

    @property
    def schema_registry(self) -> EventSchemaRegistry | None:
        """Get the schema registry (if validation enabled)."""
        return self._schema_registry

    def __repr__(self) -> str:
        """
        Return a string representation of the EventBus state.

        Returns:
            A string showing handler count and published event count.
        """
        total_handlers = sum(len(h) for h in self._handlers.values())
        total_handlers += sum(len(h) for h in self._wildcard_handlers.values())
        return f"<EventBus handlers={total_handlers} events_published={self._stats.events_published}>"


# Global singleton accessors live in a sibling module to keep this file under
# the 500-LOC cap; re-exported here so the import path is unchanged.
from core.events._singleton import get_event_bus, reset_event_bus  # noqa: E402

__all__ = [
    "EventBus",
    "get_event_bus",
    "reset_event_bus",
]
