"""
Human-in-the-Loop (HITL) Collaborative Patterns.

Implements the protocol and management logic for agent-human
interaction. Orchestrates structured requests for approval,
clarification, and selection, enabling 'Collaborative Intelligence' by
allowing agents to safely query humans during ambiguous or critical tasks.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from uuid import UUID, uuid4

from core.observability.logging import get_logger

logger = get_logger(__name__)


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
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"
    TIMEOUT = "timeout"


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
    data: Dict[str, Any] = field(default_factory=dict)
    options: Optional[List[str]] = None
    timeout_seconds: Optional[int] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: InteractionStatus = InteractionStatus.PENDING
    response: Optional[Any] = None


class HumanIntervention:
    """
    Manager for coordinated human intervention.

    Facilitates the request-response lifecycle for human participation
    in agentic workflows. Supports asynchronous wait-for-response with
    timeouts, automated rejection on disconnection, and structured
    callback hooks for various interface adapters (UI, CLI, Chat).
    """

    def __init__(self, callback: Optional[Callable[[HumanRequest], Any]] = None):
        """Initialize with an optional callback handler.

        Args:
            callback: Function to call when a request is made.
                Usually connects to a UI or Chat interface.
                Can be sync or async.
        """
        self.callback = callback
        self._pending_requests: Dict[UUID, HumanRequest] = {}

    async def request_approval(
        self,
        action_description: str,
        timeout: Optional[int] = None,
        context: Optional[Dict[str, Any]] = None,
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
        timeout: Optional[int] = None,
        context: Optional[Dict[str, Any]] = None,
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
        options: List[str],
        timeout: Optional[int] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
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
        context: Optional[Dict[str, Any]] = None,
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

    async def _process_request(self, request: HumanRequest) -> Any:
        """Internal handling of the request lifecycle.

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
                if asyncio.iscoroutinefunction(self.callback):
                    coro = self.callback(request)
                    if request.timeout_seconds is not None:
                        response = await asyncio.wait_for(
                            coro, timeout=request.timeout_seconds
                        )
                    else:
                        response = await coro
                else:
                    response = self.callback(request)

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

        except asyncio.TimeoutError:
            logger.warning(
                "Human intervention timed out",
                request_id=str(request.id),
                timeout_seconds=request.timeout_seconds,
            )
            request.status = InteractionStatus.TIMEOUT
            request.response = None
            return None

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
            if request.id in self._pending_requests:
                del self._pending_requests[request.id]

    def get_pending_requests(self) -> List[HumanRequest]:
        """Get all pending interaction requests.

        Returns:
            List of HumanRequest objects that are still pending.
        """
        return list(self._pending_requests.values())

    def has_pending_requests(self) -> bool:
        """Check if there are any pending requests.

        Returns:
            True if there are pending requests, False otherwise.
        """
        return len(self._pending_requests) > 0
