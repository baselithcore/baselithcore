"""
A2A (Agent-to-Agent) Protocol Module

Provides inter-agent communication capabilities per Google A2A specification:
- AgentCard and AgentSkill for agent discovery
- A2A types (Message, Task, Artifact, Parts)
- JSON-RPC 2.0 protocol support
- Agent discovery service with health tracking
- A2A client with retry and circuit breaker
- A2A server base with FastAPI router
"""

# Agent Card
from .agent_card import (
    AgentCard,
    AgentCapability,
    AgentCapabilities,
    AgentSkill,
)

# Discovery
from .discovery import AgentDiscovery, AgentRegistration

# Types
from .types import (
    Artifact,
    DataPart,
    FileContent,
    FilePart,
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
    part_from_dict,
)

# Protocol
from .protocol import (
    A2AMessage,
    A2AMethod,
    A2ARequest,
    A2AResponse,
    ErrorCode,
    JSONRPCError,
    JSONRPCRequest,
    JSONRPCResponse,
    MessageType,
)

# Client
from .client import A2AClient, A2AClientConfig, A2AClientPool

# Server
from .server import (
    A2AServer,
    EchoA2AServer,
    InMemoryTaskStore,
    TaskStore,
)

# Router
from .router import create_a2a_router, create_standalone_app


__all__ = [
    # Agent Card
    "AgentCard",
    "AgentCapability",
    "AgentCapabilities",
    "AgentSkill",
    # Discovery
    "AgentDiscovery",
    "AgentRegistration",
    # Types
    "Artifact",
    "DataPart",
    "FileContent",
    "FilePart",
    "Message",
    "Part",
    "Role",
    "Task",
    "TaskState",
    "TaskStatus",
    "TextPart",
    "part_from_dict",
    # Protocol
    "A2AMessage",
    "A2AMethod",
    "A2ARequest",
    "A2AResponse",
    "ErrorCode",
    "JSONRPCError",
    "JSONRPCRequest",
    "JSONRPCResponse",
    "MessageType",
    # Client
    "A2AClient",
    "A2AClientConfig",
    "A2AClientPool",
    # Server
    "A2AServer",
    "EchoA2AServer",
    "InMemoryTaskStore",
    "TaskStore",
    # Router
    "create_a2a_router",
    "create_standalone_app",
]
