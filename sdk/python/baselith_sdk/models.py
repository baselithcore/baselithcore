"""Typed request/response models for the BaselithCore SDK.

Mirrors the server's public contract (``core.models.chat``). Kept as a small,
self-contained set of Pydantic v2 models so the SDK has no dependency on the
server package.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    """A query to the agent (`POST /chat`)."""

    query: str = Field(..., min_length=1, max_length=8000)
    conversation_id: Optional[str] = None
    rag_only: bool = False
    kb_label: Optional[str] = None
    tenant_id: Optional[str] = None
    max_response_tokens: Optional[int] = Field(default=None, ge=1, le=16000)

    model_config = ConfigDict(extra="forbid")


class ChatResponse(BaseModel):
    """The agent's answer (`POST /chat`)."""

    answer: str
    metadata: Optional[Dict[str, Any]] = None
    sources: Optional[List[Dict[str, Any]]] = None
    conversation_id: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class FeedbackRequest(BaseModel):
    """Feedback on a generated answer (`POST /feedback`)."""

    query: str = Field(..., min_length=1, max_length=8000)
    answer: str = Field(..., min_length=1, max_length=32000)
    feedback: Literal["positive", "negative"]
    conversation_id: Optional[str] = None
    sources: Optional[List[Dict[str, Any]]] = None
    comment: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class HealthStatus(BaseModel):
    """Liveness response (`GET /health`)."""

    status: str

    model_config = ConfigDict(extra="allow")


class ReadinessStatus(BaseModel):
    """Readiness response (`GET /health/ready`)."""

    status: str
    services: Dict[str, bool] = Field(default_factory=dict)
    cached: bool = False

    model_config = ConfigDict(extra="allow")
