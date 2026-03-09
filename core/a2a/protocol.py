"""
Agent-to-Agent (A2A) Communication Protocol.

Defines the semantic handshake and messaging standards for inter-agent
collaboration. Enables decentralized swarms where autonomous entities
can discover, negotiate, and exchange structured knowledge and task
delegations over a standardized wire format.

Based on JSON-RPC 2.0 as per Google A2A specification.
"""

import uuid
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Union
from enum import Enum


# =============================================================================
# Message Type (Legacy)
# =============================================================================


class MessageType(str, Enum):
    """A2A message types (legacy)."""

    REQUEST = "request"
    RESPONSE = "response"
    NOTIFICATION = "notification"
    ERROR = "error"


# =============================================================================
# A2A Methods (Per Google A2A Spec)
# =============================================================================


class A2AMethod(str, Enum):
    """
    Standard A2A RPC methods per Google A2A specification.

    Methods:
        MESSAGE_SEND: Send a message (sync request/response)
        MESSAGE_STREAM: Send a message with SSE streaming
        TASKS_GET: Get task status and results
        TASKS_CANCEL: Cancel a running task
        TASKS_RESUBSCRIBE: Resubscribe to task updates
        TASKS_PUSH_NOTIFICATION_SET: Set push notification config
        TASKS_PUSH_NOTIFICATION_GET: Get push notification config
    """

    MESSAGE_SEND = "message/send"
    MESSAGE_STREAM = "message/stream"
    TASKS_GET = "tasks/get"
    TASKS_CANCEL = "tasks/cancel"
    TASKS_RESUBSCRIBE = "tasks/resubscribe"
    TASKS_PUSH_NOTIFICATION_SET = "tasks/pushNotification/set"
    TASKS_PUSH_NOTIFICATION_GET = "tasks/pushNotification/get"


# =============================================================================
# Error Codes
# =============================================================================


class ErrorCode:
    """
    Standard A2A error codes.

    Based on JSON-RPC 2.0 standard codes plus A2A-specific codes.
    """

    # JSON-RPC 2.0 standard errors
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603

    # A2A specific errors (Server error range: -32099 to -32000)
    AGENT_UNAVAILABLE = -32000
    TIMEOUT = -32001
    CAPABILITY_NOT_FOUND = -32002
    TASK_NOT_FOUND = -32003
    TASK_NOT_CANCELABLE = -32004
    UNSUPPORTED_OPERATION = -32005
    CONTENT_TYPE_NOT_SUPPORTED = -32006
    PUSH_NOTIFICATION_NOT_SUPPORTED = -32007


# =============================================================================
# JSON-RPC 2.0 Structures
# =============================================================================


@dataclass
class JSONRPCError:
    """
    JSON-RPC 2.0 Error object.

    Per spec: https://www.jsonrpc.org/specification#error_object
    """

    code: int
    message: str
    data: Optional[Any] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        result: Dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.data is not None:
            result["data"] = self.data
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JSONRPCError":
        """Deserialize from dictionary."""
        return cls(
            code=data["code"],
            message=data["message"],
            data=data.get("data"),
        )

    # Factory methods for common errors
    @classmethod
    def parse_error(cls, data: Optional[Any] = None) -> "JSONRPCError":
        """Create parse error."""
        return cls(ErrorCode.PARSE_ERROR, "Parse error", data)

    @classmethod
    def invalid_request(cls, data: Optional[Any] = None) -> "JSONRPCError":
        """Create invalid request error."""
        return cls(ErrorCode.INVALID_REQUEST, "Invalid Request", data)

    @classmethod
    def method_not_found(cls, method: str) -> "JSONRPCError":
        """Create method not found error."""
        return cls(ErrorCode.METHOD_NOT_FOUND, f"Method not found: {method}", method)

    @classmethod
    def invalid_params(cls, data: Optional[Any] = None) -> "JSONRPCError":
        """Create invalid params error."""
        return cls(ErrorCode.INVALID_PARAMS, "Invalid params", data)

    @classmethod
    def internal_error(cls, data: Optional[Any] = None) -> "JSONRPCError":
        """Create internal error."""
        return cls(ErrorCode.INTERNAL_ERROR, "Internal error", data)

    @classmethod
    def task_not_found(cls, task_id: str) -> "JSONRPCError":
        """Create task not found error."""
        return cls(ErrorCode.TASK_NOT_FOUND, f"Task not found: {task_id}", task_id)


@dataclass
class JSONRPCRequest:
    """
    JSON-RPC 2.0 Request object.

    Per spec: https://www.jsonrpc.org/specification#request_object
    """

    method: str
    id: Union[str, int] = field(default_factory=lambda: str(uuid.uuid4()))
    params: Optional[Dict[str, Any]] = None
    jsonrpc: str = field(default="2.0", init=False)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        result: Dict[str, Any] = {
            "jsonrpc": self.jsonrpc,
            "method": self.method,
            "id": self.id,
        }
        if self.params is not None:
            result["params"] = self.params
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JSONRPCRequest":
        """Deserialize from dictionary."""
        return cls(
            method=data["method"],
            id=data.get("id", str(uuid.uuid4())),
            params=data.get("params"),
        )

    @classmethod
    def message_send(
        cls,
        message: Dict[str, Any],
        context_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "JSONRPCRequest":
        """Create a message/send request."""
        params: Dict[str, Any] = {"message": message}
        if context_id:
            params["contextId"] = context_id
        if metadata:
            params["metadata"] = metadata
        return cls(method=A2AMethod.MESSAGE_SEND.value, params=params)

    @classmethod
    def tasks_get(
        cls,
        task_id: str,
        history_length: Optional[int] = None,
    ) -> "JSONRPCRequest":
        """Create a tasks/get request."""
        params: Dict[str, Any] = {"id": task_id}
        if history_length is not None:
            params["historyLength"] = history_length
        return cls(method=A2AMethod.TASKS_GET.value, params=params)

    @classmethod
    def tasks_cancel(cls, task_id: str) -> "JSONRPCRequest":
        """Create a tasks/cancel request."""
        return cls(method=A2AMethod.TASKS_CANCEL.value, params={"id": task_id})


@dataclass
class JSONRPCResponse:
    """
    JSON-RPC 2.0 Response object.

    Per spec: https://www.jsonrpc.org/specification#response_object
    Either result or error must be present, but not both.
    """

    id: Union[str, int, None]
    result: Optional[Any] = None
    error: Optional[JSONRPCError] = None
    jsonrpc: str = field(default="2.0", init=False)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        data: Dict[str, Any] = {
            "jsonrpc": self.jsonrpc,
            "id": self.id,
        }
        if self.error is not None:
            data["error"] = self.error.to_dict()
        else:
            data["result"] = self.result
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JSONRPCResponse":
        """Deserialize from dictionary."""
        error = None
        if "error" in data:
            error = JSONRPCError.from_dict(data["error"])
        return cls(
            id=data.get("id"),
            result=data.get("result"),
            error=error,
        )

    @classmethod
    def success(cls, request_id: Union[str, int], result: Any) -> "JSONRPCResponse":
        """Create a success response."""
        return cls(id=request_id, result=result)

    @classmethod
    def failure(
        cls,
        request_id: Union[str, int, None],
        error: JSONRPCError,
    ) -> "JSONRPCResponse":
        """Create an error response."""
        return cls(id=request_id, error=error)

    @property
    def is_success(self) -> bool:
        """Check if response is successful."""
        return self.error is None


# =============================================================================
# Legacy A2A Message (Backward Compatible)
# =============================================================================


@dataclass
class A2AMessage:
    """
    Standard A2A protocol message (legacy format).

    Supports request/response patterns and notifications.
    For new code, prefer JSONRPCRequest/JSONRPCResponse.
    """

    type: MessageType
    method: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    params: Optional[Dict[str, Any]] = None
    result: Optional[Any] = None
    error: Optional[Dict[str, Any]] = None
    timestamp: float = field(default_factory=time.time)

    # Routing
    from_agent: Optional[str] = None
    to_agent: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        data: Dict[str, Any] = {
            "type": self.type.value,
            "method": self.method,
            "id": self.id,
            "timestamp": self.timestamp,
        }
        if self.params is not None:
            data["params"] = self.params
        if self.result is not None:
            data["result"] = self.result
        if self.error is not None:
            data["error"] = self.error
        if self.from_agent:
            data["from_agent"] = self.from_agent
        if self.to_agent:
            data["to_agent"] = self.to_agent
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "A2AMessage":
        """Deserialize from dictionary."""
        return cls(
            type=MessageType(data["type"]),
            method=data["method"],
            id=data.get("id", str(uuid.uuid4())),
            params=data.get("params"),
            result=data.get("result"),
            error=data.get("error"),
            timestamp=data.get("timestamp", time.time()),
            from_agent=data.get("from_agent"),
            to_agent=data.get("to_agent"),
        )

    @classmethod
    def request(
        cls,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        from_agent: Optional[str] = None,
        to_agent: Optional[str] = None,
    ) -> "A2AMessage":
        """Create a request message."""
        return cls(
            type=MessageType.REQUEST,
            method=method,
            params=params,
            from_agent=from_agent,
            to_agent=to_agent,
        )

    @classmethod
    def response(
        cls,
        request_id: str,
        result: Any,
        from_agent: Optional[str] = None,
    ) -> "A2AMessage":
        """Create a response message."""
        return cls(
            type=MessageType.RESPONSE,
            method="response",
            id=request_id,
            result=result,
            from_agent=from_agent,
        )

    @classmethod
    def error_response(
        cls,
        request_id: str,
        code: int,
        message: str,
        data: Optional[Any] = None,
    ) -> "A2AMessage":
        """Create an error response."""
        return cls(
            type=MessageType.ERROR,
            method="error",
            id=request_id,
            error={"code": code, "message": message, "data": data},
        )


# =============================================================================
# Legacy Request/Response Wrappers
# =============================================================================


@dataclass
class A2ARequest:
    """
    High-level request wrapper (legacy).

    Provides convenience methods for common patterns.
    For new code, prefer JSONRPCRequest.
    """

    method: str
    params: Dict[str, Any] = field(default_factory=dict)
    timeout: float = 30.0
    retries: int = 3

    def to_message(
        self,
        from_agent: Optional[str] = None,
        to_agent: Optional[str] = None,
    ) -> A2AMessage:
        """Convert to A2A message."""
        return A2AMessage.request(
            method=self.method,
            params=self.params,
            from_agent=from_agent,
            to_agent=to_agent,
        )


@dataclass
class A2AResponse:
    """
    High-level response wrapper (legacy).

    For new code, prefer JSONRPCResponse.
    """

    success: bool
    result: Optional[Any] = None
    error_code: Optional[int] = None
    error_message: Optional[str] = None
    latency_ms: float = 0.0

    @classmethod
    def from_message(cls, msg: A2AMessage, latency_ms: float = 0.0) -> "A2AResponse":
        """Create from A2A message."""
        if msg.type == MessageType.ERROR:
            return cls(
                success=False,
                error_code=msg.error.get("code") if msg.error else None,
                error_message=msg.error.get("message") if msg.error else None,
                latency_ms=latency_ms,
            )
        return cls(success=True, result=msg.result, latency_ms=latency_ms)
