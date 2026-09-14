"""Typed response models for the A2A HTTP surface.

The A2A routes returned bare ``dict[str, Any]`` (and, on the JSON-RPC
endpoint, ``Any``), so every generated client and the published OpenAPI
document described them as "an object" — a peer integrating against the schema
learned nothing about the agent card, the health probe or the JSON-RPC
envelope it would receive.

The card model is deliberately ``extra="allow"``: the A2A card is an open
object (deployments add their own members, and later spec revisions add more),
and a closed response model would silently *drop* whatever it did not declare
on the way out. Declaring the known members documents them without making the
model the arbiter of what may be served.

Kept out of :mod:`core.a2a.router` so that module stays under the 500-line cap.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentCardResponse(BaseModel):
    """The agent card as served at the discovery endpoints.

    Mirrors :meth:`core.a2a.agent_card.AgentCard.to_dict`; unknown members are
    passed through untouched.
    """

    model_config = ConfigDict(extra="allow")

    name: str = Field(description="Unique agent name.")
    description: str = Field(description="What this agent does.")
    version: str = Field(description="The agent's own semantic version.")
    protocolVersion: str = Field(
        default="0.3.0", description="A2A protocol revision this agent implements."
    )
    url: str | None = Field(default=None, description="Base URL for A2A communication.")
    preferredTransport: str | None = Field(
        default=None, description="Transport a peer should try first."
    )
    capabilities: dict[str, Any] = Field(
        default_factory=dict,
        description="Protocol features: streaming, pushNotifications, "
        "stateTransitionHistory.",
    )
    skills: list[dict[str, Any]] | None = Field(
        default=None, description="A2A skill definitions this agent offers."
    )
    defaultInputModes: list[str] = Field(
        default_factory=list, description="Content types the agent accepts."
    )
    defaultOutputModes: list[str] = Field(
        default_factory=list, description="Content types the agent produces."
    )
    securitySchemes: dict[str, Any] | None = Field(
        default=None, description="Named OpenAPI security schemes the agent accepts."
    )
    security: list[dict[str, list[str]]] | None = Field(
        default=None, description="Which of those schemes a peer must satisfy."
    )
    documentationUrl: str | None = Field(
        default=None, description="Human-readable documentation for this agent."
    )


class A2AHealthResponse(BaseModel):
    """Liveness of the agent behind the A2A endpoint."""

    status: str = Field(description='"healthy" while the agent is serving.')
    agent: str = Field(description="The agent's name, from its card.")
    version: str = Field(description="The agent's version, from its card.")


class JSONRPCErrorModel(BaseModel):
    """A JSON-RPC 2.0 error object."""

    code: int = Field(description="JSON-RPC or A2A error code.")
    message: str = Field(description="Human-readable summary.")
    data: Any | None = Field(
        default=None, description="Optional machine-readable detail."
    )


class JSONRPCResponseModel(BaseModel):
    """A JSON-RPC 2.0 response envelope.

    Exactly one of ``result`` / ``error`` is present. On ``message/stream``
    the endpoint answers with ``text/event-stream`` instead, one of these
    envelopes per ``data:`` frame.
    """

    model_config = ConfigDict(extra="allow")

    jsonrpc: str = Field(default="2.0", description='Always "2.0".')
    id: str | int | None = Field(
        default=None, description="Echoes the request id; null when unknown."
    )
    result: Any | None = Field(default=None, description="Present on success.")
    error: JSONRPCErrorModel | None = Field(
        default=None, description="Present on failure."
    )


__all__ = [
    "A2AHealthResponse",
    "AgentCardResponse",
    "JSONRPCErrorModel",
    "JSONRPCResponseModel",
]
