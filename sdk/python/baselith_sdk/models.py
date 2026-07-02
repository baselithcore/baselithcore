"""Typed request/response models for the BaselithCore SDK.

Mirrors the server's public contract (``core.models.chat``). Kept as a small,
self-contained set of Pydantic v2 models so the SDK has no dependency on the
server package.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    """A query to the agent (`POST /chat`)."""

    query: str = Field(..., min_length=1, max_length=8000)
    conversation_id: str | None = None
    rag_only: bool = False
    kb_label: str | None = None
    tenant_id: str | None = None
    max_response_tokens: int | None = Field(default=None, ge=1, le=16000)

    model_config = ConfigDict(extra="forbid")


class ChatResponse(BaseModel):
    """The agent's answer (`POST /chat`)."""

    answer: str
    metadata: dict[str, Any] | None = None
    sources: list[dict[str, Any]] | None = None
    conversation_id: str | None = None

    model_config = ConfigDict(extra="allow")


class FeedbackRequest(BaseModel):
    """Feedback on a generated answer (`POST /feedback`)."""

    query: str = Field(..., min_length=1, max_length=8000)
    answer: str = Field(..., min_length=1, max_length=32000)
    feedback: Literal["positive", "negative"]
    conversation_id: str | None = None
    sources: list[dict[str, Any]] | None = None
    comment: str | None = None

    model_config = ConfigDict(extra="allow")


class HealthStatus(BaseModel):
    """Liveness response (`GET /health`)."""

    status: str

    model_config = ConfigDict(extra="allow")


class ReadinessStatus(BaseModel):
    """Readiness response (`GET /health/ready`)."""

    status: str
    services: dict[str, bool] = Field(default_factory=dict)
    cached: bool = False

    model_config = ConfigDict(extra="allow")
