"""
A2A Server

Base server implementation for hosting A2A-compatible agents.
Provides message handling, task management, and JSON-RPC dispatch.
"""

from core.observability.logging import get_logger
import uuid
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from .agent_card import AgentCard
from .protocol import (
    A2AMethod,
    ErrorCode,
    JSONRPCError,
    JSONRPCRequest,
    JSONRPCResponse,
)
from .types import (
    Artifact,
    Message,
    Task,
    TaskState,
)

logger = get_logger(__name__)


# =============================================================================
# Task Store Protocol
# =============================================================================


class TaskStore(ABC):
    """Abstract interface for task storage."""

    @abstractmethod
    async def get(self, task_id: str) -> Optional[Task]:
        """Get a task by ID."""
        ...

    @abstractmethod
    async def save(self, task: Task) -> None:
        """Save a task."""
        ...

    @abstractmethod
    async def delete(self, task_id: str) -> bool:
        """Delete a task. Returns True if deleted."""
        ...


class InMemoryTaskStore(TaskStore):
    """In-memory task storage (for development/testing)."""

    def __init__(self) -> None:
        """Initialize in-memory store."""
        self._tasks: Dict[str, Task] = {}

    async def get(self, task_id: str) -> Optional[Task]:
        """Get a task by ID."""
        return self._tasks.get(task_id)

    async def save(self, task: Task) -> None:
        """Save a task."""
        self._tasks[task.id] = task

    async def delete(self, task_id: str) -> bool:
        """Delete a task."""
        if task_id in self._tasks:
            del self._tasks[task_id]
            return True
        return False


# =============================================================================
# A2A Server
# =============================================================================


class A2AServer(ABC):
    """
    Base A2A server implementation.

    Provides:
    - JSON-RPC 2.0 request dispatching
    - Task management (create, get, cancel)
    - Message handling hooks

    Subclasses must implement:
    - handle_message: Process incoming messages and return tasks

    Example:
        ```python
        class MyAgent(A2AServer):
            async def handle_message(self, message, context_id):
                # Process the message
                task = Task.create(TaskState.WORKING, context_id)
                # ... do work ...
                task.update_state(TaskState.COMPLETED)
                return task

        server = MyAgent(agent_card)
        response = await server.dispatch(request_dict)
        ```
    """

    def __init__(
        self,
        agent_card: AgentCard,
        task_store: Optional[TaskStore] = None,
    ) -> None:
        """
        Initialize A2A server.

        Args:
            agent_card: Agent metadata card
            task_store: Optional task store (defaults to in-memory)
        """
        self.agent_card = agent_card
        self.task_store = task_store or InMemoryTaskStore()

    # -------------------------------------------------------------------------
    # JSON-RPC Dispatch
    # -------------------------------------------------------------------------

    async def dispatch(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Dispatch a JSON-RPC request to the appropriate handler.

        Args:
            request_data: Raw JSON-RPC request dictionary

        Returns:
            JSON-RPC response dictionary
        """
        try:
            request = JSONRPCRequest.from_dict(request_data)
        except (KeyError, ValueError) as e:
            logger.warning(f"Invalid JSON-RPC request: {e}")
            return JSONRPCResponse.failure(
                None,
                JSONRPCError.invalid_request(str(e)),
            ).to_dict()

        try:
            response = await self._handle_method(request)
            return response.to_dict()
        except Exception as e:
            logger.exception(f"Error handling request {request.method}: {e}")
            return JSONRPCResponse.failure(
                request.id,
                JSONRPCError.internal_error(str(e)),
            ).to_dict()

    async def _handle_method(self, request: JSONRPCRequest) -> JSONRPCResponse:
        """Route request to appropriate handler."""
        method = request.method

        if method == A2AMethod.MESSAGE_SEND.value:
            return await self._handle_message_send(request)
        elif method == A2AMethod.TASKS_GET.value:
            return await self._handle_tasks_get(request)
        elif method == A2AMethod.TASKS_CANCEL.value:
            return await self._handle_tasks_cancel(request)
        elif method == A2AMethod.MESSAGE_STREAM.value:
            # Streaming not implemented yet
            return JSONRPCResponse.failure(
                request.id,
                JSONRPCError(
                    ErrorCode.UNSUPPORTED_OPERATION,
                    "Streaming not supported",
                    {"operation": method},
                ),
            )
        else:
            return JSONRPCResponse.failure(
                request.id,
                JSONRPCError.method_not_found(method),
            )

    # -------------------------------------------------------------------------
    # Method Handlers
    # -------------------------------------------------------------------------

    async def _handle_message_send(
        self,
        request: JSONRPCRequest,
    ) -> JSONRPCResponse:
        """Handle message/send requests."""
        params = request.params or {}

        # Validate message
        message_data = params.get("message")
        if not message_data:
            return JSONRPCResponse.failure(
                request.id,
                JSONRPCError.invalid_params("Missing 'message' in params"),
            )

        # Parse message
        try:
            message = Message.from_dict(message_data)
        except (KeyError, ValueError) as e:
            return JSONRPCResponse.failure(
                request.id,
                JSONRPCError.invalid_params(f"Invalid message format: {e}"),
            )

        # Get or create context
        context_id = params.get("contextId") or str(uuid.uuid4())
        metadata = params.get("metadata")

        # Process message
        try:
            task = await self.handle_message(message, context_id, metadata)
            await self.task_store.save(task)
            return JSONRPCResponse.success(request.id, task.to_dict())
        except Exception as e:
            logger.exception(f"Error processing message: {e}")
            return JSONRPCResponse.failure(
                request.id,
                JSONRPCError.internal_error(str(e)),
            )

    async def _handle_tasks_get(self, request: JSONRPCRequest) -> JSONRPCResponse:
        """Handle tasks/get requests."""
        params = request.params or {}

        task_id = params.get("id")
        if not task_id:
            return JSONRPCResponse.failure(
                request.id,
                JSONRPCError.invalid_params("Missing 'id' in params"),
            )

        task = await self.task_store.get(task_id)
        if not task:
            return JSONRPCResponse.failure(
                request.id,
                JSONRPCError.task_not_found(task_id),
            )

        return JSONRPCResponse.success(request.id, task.to_dict())

    async def _handle_tasks_cancel(self, request: JSONRPCRequest) -> JSONRPCResponse:
        """Handle tasks/cancel requests."""
        params = request.params or {}

        task_id = params.get("id")
        if not task_id:
            return JSONRPCResponse.failure(
                request.id,
                JSONRPCError.invalid_params("Missing 'id' in params"),
            )

        task = await self.task_store.get(task_id)
        if not task:
            return JSONRPCResponse.failure(
                request.id,
                JSONRPCError.task_not_found(task_id),
            )

        # Check if task can be canceled
        if task.is_terminal:
            return JSONRPCResponse.failure(
                request.id,
                JSONRPCError(
                    ErrorCode.TASK_NOT_CANCELABLE,
                    f"Task {task_id} is already in terminal state: {task.status.state.value}",
                    {"taskId": task_id, "state": task.status.state.value},
                ),
            )

        # Cancel the task
        task.update_state(TaskState.CANCELED)
        await self.task_store.save(task)

        return JSONRPCResponse.success(request.id, task.to_dict())

    # -------------------------------------------------------------------------
    # Abstract Methods (Implement in Subclass)
    # -------------------------------------------------------------------------

    @abstractmethod
    async def handle_message(
        self,
        message: Message,
        context_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Task:
        """
        Process an incoming message and return a task.

        This is the main entry point for message handling.
        Subclasses must implement this method.

        Args:
            message: The incoming message
            context_id: Context/conversation ID
            metadata: Optional request metadata

        Returns:
            Task with status and optional artifacts
        """
        ...

    # -------------------------------------------------------------------------
    # Utility Methods
    # -------------------------------------------------------------------------

    def create_task(
        self,
        state: TaskState = TaskState.SUBMITTED,
        context_id: Optional[str] = None,
    ) -> Task:
        """Create a new task."""
        return Task.create(state=state, context_id=context_id)

    def create_text_artifact(
        self,
        text: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Artifact:
        """Create a text artifact."""
        return Artifact.text_artifact(text, name, description)


# =============================================================================
# Simple Echo Server (Example/Testing)
# =============================================================================


class EchoA2AServer(A2AServer):
    """
    Simple echo server for testing.

    Echoes back the received message content.
    """

    async def handle_message(
        self,
        message: Message,
        context_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Task:
        """Echo back the message."""
        task = self.create_task(TaskState.WORKING, context_id)

        # Extract text from message
        text_content = ""
        for part in message.parts:
            if hasattr(part, "text"):
                text_content += part.text

        # Create response
        response = Message.agent_message(f"Echo: {text_content}")
        task.add_message(response)

        # Create artifact with echo
        artifact = self.create_text_artifact(
            text=text_content,
            name="echo_result",
            description="Echoed input text",
        )
        task.add_artifact(artifact)

        # Complete task
        task.update_state(TaskState.COMPLETED, response)

        return task
