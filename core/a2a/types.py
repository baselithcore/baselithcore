"""
A2A Core Types

Core data types for the Google A2A (Agent-to-Agent) protocol.
Based on the official A2A specification.
"""

import uuid
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union
from enum import Enum


# =============================================================================
# Role Enum
# =============================================================================


class Role(str, Enum):
    """Message sender role."""

    USER = "user"
    AGENT = "agent"


# =============================================================================
# Task State Enum
# =============================================================================


class TaskState(str, Enum):
    """
    Task lifecycle states per A2A specification.

    States:
        SUBMITTED: Task received, not yet started
        WORKING: Task is actively being processed
        INPUT_REQUIRED: Agent needs additional input from user
        COMPLETED: Task finished successfully
        CANCELED: Task was canceled by client
        FAILED: Task failed due to an error
        REJECTED: Task was rejected by the agent
    """

    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    CANCELED = "canceled"
    FAILED = "failed"
    REJECTED = "rejected"


# =============================================================================
# Part Types (Message Components)
# =============================================================================


@dataclass
class FileContent:
    """
    File content for FilePart.

    Can contain either inline data (bytes) or a URI reference.
    """

    name: str
    mimeType: str
    bytes: Optional[str] = None  # Base64-encoded data
    uri: Optional[str] = None  # URI reference

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        data: Dict[str, Any] = {
            "name": self.name,
            "mimeType": self.mimeType,
        }
        if self.bytes is not None:
            data["bytes"] = self.bytes
        if self.uri is not None:
            data["uri"] = self.uri
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FileContent":
        """Deserialize from dictionary."""
        return cls(
            name=data["name"],
            mimeType=data["mimeType"],
            bytes=data.get("bytes"),
            uri=data.get("uri"),
        )


@dataclass
class TextPart:
    """Text content part."""

    text: str
    kind: str = field(default="text", init=False)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {"kind": self.kind, "text": self.text}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TextPart":
        """Deserialize from dictionary."""
        return cls(text=data["text"])


@dataclass
class FilePart:
    """File content part."""

    file: FileContent
    kind: str = field(default="file", init=False)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {"kind": self.kind, "file": self.file.to_dict()}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FilePart":
        """Deserialize from dictionary."""
        return cls(file=FileContent.from_dict(data["file"]))


@dataclass
class DataPart:
    """Structured data part (JSON-like)."""

    data: Dict[str, Any]
    kind: str = field(default="data", init=False)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {"kind": self.kind, "data": self.data}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DataPart":
        """Deserialize from dictionary."""
        return cls(data=data["data"])


# Union type for all part types
Part = Union[TextPart, FilePart, DataPart]


def part_from_dict(data: Dict[str, Any]) -> Part:
    """
    Deserialize a Part from dictionary based on 'kind' field.

    Args:
        data: Dictionary with 'kind' field indicating type

    Returns:
        Appropriate Part subclass instance

    Raises:
        ValueError: If kind is unknown
    """
    kind = data.get("kind")
    if kind == "text":
        return TextPart.from_dict(data)
    elif kind == "file":
        return FilePart.from_dict(data)
    elif kind == "data":
        return DataPart.from_dict(data)
    else:
        raise ValueError(f"Unknown part kind: {kind}")


# =============================================================================
# Message
# =============================================================================


@dataclass
class Message:
    """
    A2A Message object.

    Represents a single communication turn between client and agent.
    """

    role: Role
    parts: List[Part]
    messageId: str = field(default_factory=lambda: str(uuid.uuid4()))
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        data: Dict[str, Any] = {
            "role": self.role.value,
            "parts": [p.to_dict() for p in self.parts],
            "messageId": self.messageId,
        }
        if self.metadata is not None:
            data["metadata"] = self.metadata
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        """Deserialize from dictionary."""
        return cls(
            role=Role(data["role"]),
            parts=[part_from_dict(p) for p in data.get("parts", [])],
            messageId=data.get("messageId", str(uuid.uuid4())),
            metadata=data.get("metadata"),
        )

    @classmethod
    def user_message(cls, text: str, **kwargs: Any) -> "Message":
        """Create a simple user text message."""
        return cls(role=Role.USER, parts=[TextPart(text=text)], **kwargs)

    @classmethod
    def agent_message(cls, text: str, **kwargs: Any) -> "Message":
        """Create a simple agent text message."""
        return cls(role=Role.AGENT, parts=[TextPart(text=text)], **kwargs)


# =============================================================================
# Task Status
# =============================================================================


@dataclass
class TaskStatus:
    """
    Current status of a task.

    Includes state, optional message, and timestamp.
    """

    state: TaskState
    message: Optional[Message] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        data: Dict[str, Any] = {
            "state": self.state.value,
            "timestamp": self.timestamp,
        }
        if self.message is not None:
            data["message"] = self.message.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskStatus":
        """Deserialize from dictionary."""
        return cls(
            state=TaskState(data["state"]),
            message=Message.from_dict(data["message"]) if data.get("message") else None,
            timestamp=data.get("timestamp", time.time()),
        )


# =============================================================================
# Artifact
# =============================================================================


@dataclass
class Artifact:
    """
    Task output artifact.

    Represents a piece of output generated by the agent.
    """

    artifactId: str
    parts: List[Part]
    name: Optional[str] = None
    description: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        data: Dict[str, Any] = {
            "artifactId": self.artifactId,
            "parts": [p.to_dict() for p in self.parts],
        }
        if self.name is not None:
            data["name"] = self.name
        if self.description is not None:
            data["description"] = self.description
        if self.metadata is not None:
            data["metadata"] = self.metadata
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Artifact":
        """Deserialize from dictionary."""
        return cls(
            artifactId=data["artifactId"],
            parts=[part_from_dict(p) for p in data.get("parts", [])],
            name=data.get("name"),
            description=data.get("description"),
            metadata=data.get("metadata"),
        )

    @classmethod
    def text_artifact(
        cls,
        text: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> "Artifact":
        """Create a simple text artifact."""
        return cls(
            artifactId=str(uuid.uuid4()),
            parts=[TextPart(text=text)],
            name=name,
            description=description,
        )


# =============================================================================
# Task
# =============================================================================


@dataclass
class Task:
    """
    A2A Task object.

    Represents a unit of work being performed by an agent.
    """

    id: str
    status: TaskStatus
    contextId: Optional[str] = None
    artifacts: List[Artifact] = field(default_factory=list)
    history: List[Message] = field(default_factory=list)
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        data: Dict[str, Any] = {
            "id": self.id,
            "status": self.status.to_dict(),
            "kind": "task",
        }
        if self.contextId is not None:
            data["contextId"] = self.contextId
        if self.artifacts:
            data["artifacts"] = [a.to_dict() for a in self.artifacts]
        if self.history:
            data["history"] = [m.to_dict() for m in self.history]
        if self.metadata is not None:
            data["metadata"] = self.metadata
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        """Deserialize from dictionary."""
        return cls(
            id=data["id"],
            status=TaskStatus.from_dict(data["status"]),
            contextId=data.get("contextId"),
            artifacts=[Artifact.from_dict(a) for a in data.get("artifacts", [])],
            history=[Message.from_dict(m) for m in data.get("history", [])],
            metadata=data.get("metadata"),
        )

    @classmethod
    def create(
        cls,
        state: TaskState = TaskState.SUBMITTED,
        context_id: Optional[str] = None,
    ) -> "Task":
        """Create a new task with initial state."""
        return cls(
            id=str(uuid.uuid4()),
            status=TaskStatus(state=state),
            contextId=context_id or str(uuid.uuid4()),
        )

    def update_state(
        self,
        state: TaskState,
        message: Optional[Message] = None,
    ) -> None:
        """Update task state with optional message."""
        self.status = TaskStatus(state=state, message=message)

    def add_artifact(self, artifact: Artifact) -> None:
        """Add an artifact to the task."""
        self.artifacts.append(artifact)

    def add_message(self, message: Message) -> None:
        """Add a message to the task history."""
        self.history.append(message)

    @property
    def is_terminal(self) -> bool:
        """Check if task is in a terminal state."""
        return self.status.state in (
            TaskState.COMPLETED,
            TaskState.CANCELED,
            TaskState.FAILED,
            TaskState.REJECTED,
        )
