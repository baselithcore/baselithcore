"""Pre-JSON-RPC A2A message wrappers, kept for backward compatibility.

:class:`A2AMessage`, :class:`A2ARequest` and :class:`A2AResponse` predate the
JSON-RPC 2.0 wire format the A2A specification settled on. Nothing in the
server or router speaks them any more — new code uses
:class:`~core.a2a.protocol.JSONRPCRequest` /
:class:`~core.a2a.protocol.JSONRPCResponse` — but they remain part of the
published surface, so they live on here and are re-exported from
:mod:`core.a2a.protocol` at their original import path.

Split out of that module to keep it under the 500-line cap.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from core.a2a.protocol import MessageType

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
    params: dict[str, Any] | None = None
    result: Any | None = None
    error: dict[str, Any] | None = None
    timestamp: float = field(default_factory=time.time)

    # Routing
    from_agent: str | None = None
    to_agent: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        data: dict[str, Any] = {
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
    def from_dict(cls, data: dict[str, Any]) -> A2AMessage:
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
        params: dict[str, Any] | None = None,
        from_agent: str | None = None,
        to_agent: str | None = None,
    ) -> A2AMessage:
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
        from_agent: str | None = None,
    ) -> A2AMessage:
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
        data: Any | None = None,
    ) -> A2AMessage:
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
    params: dict[str, Any] = field(default_factory=dict)
    timeout: float = 30.0
    retries: int = 3

    def to_message(
        self,
        from_agent: str | None = None,
        to_agent: str | None = None,
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
    result: Any | None = None
    error_code: int | None = None
    error_message: str | None = None
    latency_ms: float = 0.0

    @classmethod
    def from_message(cls, msg: A2AMessage, latency_ms: float = 0.0) -> A2AResponse:
        """Create from A2A message."""
        if msg.type == MessageType.ERROR:
            return cls(
                success=False,
                error_code=msg.error.get("code") if msg.error else None,
                error_message=msg.error.get("message") if msg.error else None,
                latency_ms=latency_ms,
            )
        return cls(success=True, result=msg.result, latency_ms=latency_ms)


__all__ = [
    "A2AMessage",
    "A2ARequest",
    "A2AResponse",
]
