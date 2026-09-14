"""
Human-in-the-Loop (HITL) Collaborative Patterns.

Implements the protocol and management logic for agent-human
interaction. Orchestrates structured requests for approval,
clarification, and selection, enabling 'Collaborative Intelligence' by
allowing agents to safely query humans during ambiguous or critical tasks.
"""

import asyncio
import inspect
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from functools import partial
from typing import Any, Final
from uuid import UUID, uuid4

from core.human.executor import run_hitl_callback
from core.observability.logging import get_logger

logger = get_logger(__name__)

#: How many *terminal* requests (completed / rejected / timed out) the manager
#: remembers. The registry used to drop a request the instant it finished, so
#: nothing could read back an outcome; retaining every request forever would
#: instead be an unbounded leak on a long-lived process. 256 is enough for an
#: operator to inspect the recent history of a run and small enough to be free.
DEFAULT_MAX_RECENT_REQUESTS: Final[int] = 256


class InteractionType(Enum):
    """Types of human interactions.

    Attributes:
        APPROVAL: Yes/No permission request.
        INPUT: Free-form text input or clarification.
        SELECTION: Choosing from predefined options.
        NOTIFICATION: Informational notification (no response expected).
    """

    APPROVAL = "approval"
    INPUT = "input"
    SELECTION = "selection"
    NOTIFICATION = "notification"


class InteractionStatus(Enum):
    """Status of an interaction request.

    Attributes:
        PENDING: Request is awaiting human response.
        APPROVED: Request was approved (for APPROVAL type).
        REJECTED: Request was rejected or denied.
        COMPLETED: Request was successfully completed.
        TIMEOUT: Request timed out without response.
        CANCELLED: The awaiting caller was cancelled before a human answered.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class HumanRequest:
    """A request for human intervention.

    Attributes:
        type: The type of interaction requested.
        description: Human-readable description of what is being requested.
        id: Unique identifier for this request.
        data: Additional context data for the request.
        options: Available options for SELECTION type requests.
        timeout_seconds: Maximum time to wait for response.
        created_at: Timestamp when the request was created.
        status: Current status of the request.
        response: The human's response once provided.
    """

    type: InteractionType
    description: str
    id: UUID = field(default_factory=uuid4)
    data: dict[str, Any] = field(default_factory=dict)
    options: list[str] | None = None
    timeout_seconds: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    status: InteractionStatus = InteractionStatus.PENDING
    response: Any | None = None


class HumanIntervention:
    """
    Manager for coordinated human intervention.

    Facilitates the request-response lifecycle for human participation
    in agentic workflows. Supports asynchronous wait-for-response with
    timeouts, automated rejection on disconnection, and structured
    callback hooks for various interface adapters (UI, CLI, Chat).
    """

    def __init__(
        self,
        callback: Callable[[HumanRequest], Any] | None = None,
        *,
        max_recent: int = DEFAULT_MAX_RECENT_REQUESTS,
    ):
        """Initialize with an optional callback handler.

        Args:
            callback: Function to call when a request is made.
                Usually connects to a UI or Chat interface.
                Can be sync or async. A **synchronous** callback is run on a
                worker thread (:func:`asyncio.to_thread`) so a blocking UI or
                CLI prompt cannot stall the event loop.
            max_recent: How many terminal requests to retain for
                :meth:`get_recent_requests` (oldest evicted first).
        """
        self.callback = callback
        self._pending_requests: dict[UUID, HumanRequest] = {}
        self._max_recent = max(1, max_recent)
        # Insertion-ordered, bounded: terminal requests keep their status and
        # response so callers can read an outcome back after the await returns.
        self._recent_requests: OrderedDict[UUID, HumanRequest] = OrderedDict()

    async def request_approval(
        self,
        action_description: str,
        timeout: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> bool:
        """Request explicit approval for an action.

        Args:
            action_description: Human-readable description of what needs approval.
            timeout: Maximum wait time in seconds. None for no timeout.
            context: Additional context information to display.

        Returns:
            True if approved, False if rejected or timed out.

        Example:
            ```python
            approved = await intervention.request_approval(
                "Send email to 1000 users?",
                timeout=60,
                context={"template": "newsletter"}
            )
            ```
        """
        request = HumanRequest(
            type=InteractionType.APPROVAL,
            description=action_description,
            timeout_seconds=timeout,
            data=context or {},
        )

        result = await self._process_request(request)
        return bool(result) if result is not None else False

    async def ask_input(
        self,
        question: str,
        timeout: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Ask the human for textual input.

        Args:
            question: The question to ask the human.
            timeout: Maximum wait time in seconds. None for no timeout.
            context: Additional context information.

        Returns:
            The human's text response, or empty string if no response.

        Example:
            ```python
            api_key = await intervention.ask_input(
                "Please provide the API key:",
                timeout=120
            )
            ```
        """
        request = HumanRequest(
            type=InteractionType.INPUT,
            description=question,
            timeout_seconds=timeout,
            data=context or {},
        )
        result = await self._process_request(request)
        return str(result) if result else ""

    async def request_selection(
        self,
        prompt: str,
        options: list[str],
        timeout: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> str | None:
        """Present options for the human to select from.

        Args:
            prompt: Description of what the human should select.
            options: List of available options to choose from.
            timeout: Maximum wait time in seconds. None for no timeout.
            context: Additional context information.

        Returns:
            The selected option string, or None if no selection made.

        Example:
            ```python
            env = await intervention.request_selection(
                "Choose deployment target:",
                options=["staging", "production"],
                timeout=30
            )
            ```
        """
        request = HumanRequest(
            type=InteractionType.SELECTION,
            description=prompt,
            options=options,
            timeout_seconds=timeout,
            data=context or {},
        )
        result = await self._process_request(request)
        return str(result) if result and result in options else None

    async def notify(
        self,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Send a notification to the human (no response expected).

        Args:
            message: The notification message.
            context: Additional context information.

        Example:
            ```python
            await intervention.notify(
                "Background task completed successfully",
                context={"task_id": "abc123", "duration_ms": 5000}
            )
            ```
        """
        request = HumanRequest(
            type=InteractionType.NOTIFICATION,
            description=message,
            data=context or {},
        )
        await self._process_request(request)

    async def _invoke_callback(self, request: HumanRequest) -> Any:
        """Call the registered callback, off-loop when it is synchronous.

        A synchronous callback runs on the dedicated HITL pool
        (:func:`core.human.executor.run_hitl_callback`) so a blocking prompt
        (``input()``, a modal dialog, a queue ``get``) cannot freeze every
        other task sharing the loop — and so a callback that outlives its
        timeout, whose thread can never be reclaimed, burns a thread from a
        pool sized for that rather than from the interpreter default one that
        DNS lookups and audit appends also share. A callable whose return
        value is awaitable (an async ``__call__``, a lambda wrapping a
        coroutine function) is awaited too, so both shapes behave alike.
        """
        callback = self.callback
        if callback is None:  # pragma: no cover - guarded by the caller
            return None
        if asyncio.iscoroutinefunction(callback):
            return await callback(request)
        result = await run_hitl_callback(partial(callback, request))
        if inspect.isawaitable(result):
            return await result
        return result

    def _retire(self, request: HumanRequest) -> None:
        """Move a finished request from the pending registry to the archive."""
        self._pending_requests.pop(request.id, None)
        self._recent_requests.pop(request.id, None)
        self._recent_requests[request.id] = request
        while len(self._recent_requests) > self._max_recent:
            self._recent_requests.popitem(last=False)

    async def _process_request(self, request: HumanRequest) -> Any:
        """Internal handling of the request lifecycle.

        The request is visible through :meth:`get_pending_requests` while it
        is in flight and through :meth:`get_recent_requests` — carrying its
        terminal status and response — once it finishes.

        Args:
            request: The HumanRequest to process.

        Returns:
            The response from the human callback, or None if no callback
            is registered or an error occurs.
        """
        self._pending_requests[request.id] = request
        logger.info(
            "Human intervention requested",
            request_id=str(request.id),
            interaction_type=request.type.value,
            description=request.description[:100],
        )

        try:
            # Trigger callback if registered (e.g. send to UI)
            if self.callback:
                # One wait_for covers both callback shapes: the timeout used
                # to be applied to async callbacks only, so a blocking sync
                # prompt waited forever no matter what the caller asked for.
                # A timed-out *sync* callback's thread cannot be cancelled —
                # it runs to completion in the background and its result is
                # discarded; the caller is released on time regardless.
                coro = self._invoke_callback(request)
                if request.timeout_seconds is not None:
                    response = await asyncio.wait_for(
                        coro, timeout=request.timeout_seconds
                    )
                else:
                    response = await coro

                # COMPLETED means "a human answered", not "they said yes":
                # the decision itself is the retained ``response``. (REJECTED
                # is reserved for requests no human ever saw — no interface
                # connected, or the callback blew up.)
                request.status = InteractionStatus.COMPLETED
                request.response = response
                logger.debug(
                    "Human intervention completed",
                    request_id=str(request.id),
                    status=request.status.value,
                )
                return response

            # If no callback, log warning and reject
            logger.warning(
                "No human interface connected, auto-rejecting request",
                request_id=str(request.id),
            )
            request.status = InteractionStatus.REJECTED
            return None

        except TimeoutError:
            logger.warning(
                "Human intervention timed out",
                request_id=str(request.id),
                timeout_seconds=request.timeout_seconds,
            )
            request.status = InteractionStatus.TIMEOUT
            request.response = None
            return None

        except asyncio.CancelledError:
            # The caller (or its enclosing task) went away. The request is
            # still terminal — it leaves the pending registry either way — so
            # it must not be archived as PENDING, which would read as "a human
            # is still looking at it". Cancellation is never swallowed.
            logger.info(
                "Human intervention cancelled",
                request_id=str(request.id),
            )
            request.status = InteractionStatus.CANCELLED
            request.response = None
            raise

        except Exception as e:
            logger.error(
                "Error in human interaction",
                request_id=str(request.id),
                error=str(e),
                exc_info=True,
            )
            request.status = InteractionStatus.REJECTED
            return None
        finally:
            self._retire(request)

    def get_pending_requests(self) -> list[HumanRequest]:
        """Get all in-flight interaction requests.

        Returns:
            List of HumanRequest objects still awaiting a human response.
            Finished requests move to :meth:`get_recent_requests`.
        """
        return list(self._pending_requests.values())

    def get_recent_requests(
        self,
        limit: int | None = None,
        *,
        status: InteractionStatus | None = None,
    ) -> list[HumanRequest]:
        """Get recently finished interaction requests, oldest first.

        The manager retains the last ``max_recent`` terminal requests
        (default :data:`DEFAULT_MAX_RECENT_REQUESTS`) with their status and
        response, so an operator surface can report what a run asked for and
        what the human answered after the await has already returned.

        Args:
            limit: Return at most this many, keeping the most recent.
            status: Only return requests in this terminal status.

        Returns:
            List of finished HumanRequest objects in completion order.
        """
        requests = list(self._recent_requests.values())
        if status is not None:
            requests = [r for r in requests if r.status is status]
        if limit is not None:
            requests = requests[-max(0, limit) :] if limit > 0 else []
        return requests

    def get_request(self, request_id: UUID) -> HumanRequest | None:
        """Look a request up by id, in flight or recently finished.

        Args:
            request_id: The id of the request to retrieve.

        Returns:
            The HumanRequest, or None when it is unknown or has aged out of
            the bounded recent-request registry.
        """
        return self._pending_requests.get(request_id) or self._recent_requests.get(
            request_id
        )

    def has_pending_requests(self) -> bool:
        """Check if there are any in-flight requests.

        Returns:
            True if there are pending requests, False otherwise.
        """
        return len(self._pending_requests) > 0
