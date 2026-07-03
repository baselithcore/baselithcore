"""
Agent Card

Defines agent metadata for discovery and interoperability.
Based on Google A2A protocol specification.

This module provides:
- AgentCapability: Legacy capability definition (backward compatible)
- AgentSkill: A2A-compliant skill definition
- AgentCapabilities: Structured capabilities object
- AgentCard: Full agent metadata card
"""

from dataclasses import dataclass, field
from typing import Any

# =============================================================================
# Legacy Capability (Backward Compatible)
# =============================================================================


@dataclass
class AgentCapability:
    """
    A capability offered by an agent.

    This is the legacy format maintained for backward compatibility.
    For A2A-compliant definitions, use AgentSkill instead.
    """

    name: str
    description: str
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        data: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
        }
        if self.input_schema is not None:
            data["input_schema"] = self.input_schema
        if self.output_schema is not None:
            data["output_schema"] = self.output_schema
        return data


# =============================================================================
# A2A Skill (Google A2A Spec Compliant)
# =============================================================================


@dataclass
class AgentSkill:
    """
    A2A-compliant skill definition.

    Skills describe specific capabilities that an agent can perform,
    including examples of how to invoke them.

    Attributes:
        id: Unique identifier for the skill
        name: Human-readable name
        description: What this skill does
        tags: Categorization tags
        examples: Example prompts/inputs
        inputModes: Supported input content types
        outputModes: Supported output content types
    """

    id: str
    name: str
    description: str
    tags: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    inputModes: list[str] = field(default_factory=lambda: ["text/plain"])
    outputModes: list[str] = field(default_factory=lambda: ["text/plain"])

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tags": self.tags,
            "examples": self.examples,
            "inputModes": self.inputModes,
            "outputModes": self.outputModes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentSkill":
        """Deserialize from dictionary."""
        return cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description", ""),
            tags=data.get("tags", []),
            examples=data.get("examples", []),
            inputModes=data.get("inputModes", ["text/plain"]),
            outputModes=data.get("outputModes", ["text/plain"]),
        )


# =============================================================================
# A2A Capabilities Object
# =============================================================================


@dataclass
class AgentCapabilities:
    """
    Structured capabilities object per A2A spec.

    Describes what protocol features the agent supports.

    Attributes:
        streaming: Whether agent supports SSE streaming
        pushNotifications: Whether agent supports push notifications
        stateTransitionHistory: Whether agent tracks state history
    """

    streaming: bool = False
    pushNotifications: bool = False
    stateTransitionHistory: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "streaming": self.streaming,
            "pushNotifications": self.pushNotifications,
            "stateTransitionHistory": self.stateTransitionHistory,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentCapabilities":
        """Deserialize from dictionary."""
        return cls(
            streaming=data.get("streaming", False),
            pushNotifications=data.get("pushNotifications", False),
            stateTransitionHistory=data.get("stateTransitionHistory", False),
        )


# =============================================================================
# Agent Card
# =============================================================================


@dataclass
class AgentCard:
    """
    Agent metadata card for discovery.

    Compliant with Google A2A AgentCard specification.
    Maintains backward compatibility with legacy 'capabilities' field
    while also supporting new 'skills' and 'agentCapabilities' fields.

    Attributes:
        name: Unique agent name
        description: What this agent does
        version: Semantic version string
        url: Base URL for A2A communication
        endpoint: Legacy endpoint field (alias for url)
        skills: A2A-compliant skill definitions
        agentCapabilities: Protocol feature support
        capabilities: Legacy capabilities (backward compatible)
        defaultInputModes: Default supported input types
        defaultOutputModes: Default supported output types
        documentationUrl: Link to agent documentation
        protocols: Supported protocols (jsonrpc, rest, etc.)
        metadata: Additional custom metadata
    """

    name: str
    description: str
    version: str = "1.0.0"
    # A2A protocol version this agent implements (distinct from the agent's own
    # ``version``). Advertised so peers can negotiate compatible behaviour.
    protocolVersion: str = "0.3.0"

    # A2A fields
    url: str | None = None
    skills: list[AgentSkill] = field(default_factory=list)
    agentCapabilities: AgentCapabilities = field(default_factory=AgentCapabilities)

    # Content modes
    defaultInputModes: list[str] = field(default_factory=lambda: ["text/plain"])
    defaultOutputModes: list[str] = field(default_factory=lambda: ["text/plain"])

    # Documentation
    documentationUrl: str | None = None

    # Legacy fields (backward compatible)
    endpoint: str | None = None
    capabilities: list[AgentCapability] = field(default_factory=list)
    protocols: list[str] = field(default_factory=lambda: ["jsonrpc"])

    # Metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Sync legacy endpoint with url if needed."""
        if self.url is None and self.endpoint is not None:
            self.url = self.endpoint
        elif self.endpoint is None and self.url is not None:
            self.endpoint = self.url

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        data: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "protocolVersion": self.protocolVersion,
        }

        # A2A fields
        if self.url:
            data["url"] = self.url
        if self.skills:
            data["skills"] = [s.to_dict() for s in self.skills]
        data["capabilities"] = self.agentCapabilities.to_dict()

        # Content modes
        data["defaultInputModes"] = self.defaultInputModes
        data["defaultOutputModes"] = self.defaultOutputModes

        # Documentation
        if self.documentationUrl:
            data["documentationUrl"] = self.documentationUrl

        # Legacy fields (for backward compatibility)
        if self.endpoint:
            data["endpoint"] = self.endpoint
        if self.capabilities:
            data["legacyCapabilities"] = [c.to_dict() for c in self.capabilities]
        if self.protocols:
            data["protocols"] = self.protocols

        # Metadata
        if self.metadata:
            data["metadata"] = self.metadata

        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentCard":
        """Create from dictionary."""
        # Parse skills
        skills = [AgentSkill.from_dict(s) for s in data.get("skills", [])]

        # Parse agent capabilities (new format)
        caps_data = data.get("capabilities", {})
        if isinstance(caps_data, dict):
            agent_caps = AgentCapabilities.from_dict(caps_data)
        else:
            agent_caps = AgentCapabilities()

        # Parse legacy capabilities
        legacy_caps = [
            AgentCapability(**cap) for cap in data.get("legacyCapabilities", [])
        ]

        return cls(
            name=data["name"],
            description=data.get("description", ""),
            version=data.get("version", "1.0.0"),
            protocolVersion=data.get("protocolVersion", "0.3.0"),
            url=data.get("url"),
            skills=skills,
            agentCapabilities=agent_caps,
            defaultInputModes=data.get("defaultInputModes", ["text/plain"]),
            defaultOutputModes=data.get("defaultOutputModes", ["text/plain"]),
            documentationUrl=data.get("documentationUrl"),
            endpoint=data.get("endpoint"),
            capabilities=legacy_caps,
            protocols=data.get("protocols", ["jsonrpc"]),
            metadata=data.get("metadata", {}),
        )

    # -------------------------------------------------------------------------
    # Skill Management
    # -------------------------------------------------------------------------

    def add_skill(
        self,
        id: str,
        name: str,
        description: str,
        tags: list[str] | None = None,
        examples: list[str] | None = None,
    ) -> None:
        """Add a skill to the agent card."""
        self.skills.append(
            AgentSkill(
                id=id,
                name=name,
                description=description,
                tags=tags or [],
                examples=examples or [],
            )
        )

    def get_skill(self, skill_id: str) -> AgentSkill | None:
        """Get a skill by ID."""
        for skill in self.skills:
            if skill.id == skill_id:
                return skill
        return None

    def has_skill(self, skill_id: str) -> bool:
        """Check if agent has a specific skill."""
        return any(s.id == skill_id for s in self.skills)

    # -------------------------------------------------------------------------
    # Legacy Capability Management (Backward Compatible)
    # -------------------------------------------------------------------------

    def add_capability(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> None:
        """Add a legacy capability to the agent card."""
        self.capabilities.append(
            AgentCapability(
                name=name,
                description=description,
                input_schema=input_schema,
                output_schema=output_schema,
            )
        )
