"""
Chat Models.

Pydantic models for chat requests, responses, and feedback.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    """
    Request model for the agent.
    Contains the user query and, optionally, the conversation_id.
    The `stream` flag is accepted for compatibility, but the dedicated
    streaming endpoint is `/chat/stream`.
    """

    query: str = Field(..., min_length=1, max_length=8000)
    conversation_id: str | None = None
    stream: bool | None = False
    rag_only: bool = False
    kb_label: str | None = None
    tenant_id: str | None = None
    max_response_tokens: int | None = Field(
        default=None,
        ge=1,
        le=16000,
        description="Upper bound on response tokens. Hard-capped at 16000 to prevent unbounded streams.",
    )

    model_config = ConfigDict(extra="forbid")  # reject unexpected fields


class FeedbackDocumentReference(BaseModel):
    """
    Reference to a source (local file or URL) used in the response.
    - document_id: internal identifier of the indexed document
    - path/url: location of the source (filesystem or external)
    - title: human-readable description for the admin
    - source_type: category of the source (path/url)
    - score: maximum score assigned by the reranker (if available)
    """

    document_id: str | None = None
    title: str | None = None
    path: str | None = None
    url: str | None = None
    origin: str | None = None
    source_type: Literal["path", "url"] | None = None
    score: float | None = None

    model_config = ConfigDict(extra="forbid")


class FeedbackRequest(BaseModel):
    """
    Model for recording feedback on a generated response.
    - query: the question asked
    - answer: the answer provided by the bot
    - feedback: positive or negative rating
    - conversation_id: widget session (if present)
    - sources: references to the documents used in the response
    """

    query: str = Field(..., min_length=1, max_length=8000)
    answer: str = Field(..., min_length=1, max_length=32000)
    feedback: Literal["positive", "negative"]
    conversation_id: str | None = Field(default=None, max_length=128)
    sources: list[FeedbackDocumentReference | dict[str, Any]] | None = None
    comment: str | None = Field(default=None, max_length=4000)

    # Ignore (do not persist) unexpected fields rather than allowing arbitrary
    # attacker-supplied keys onto the model / into storage.
    model_config = ConfigDict(extra="ignore")


class ChatResponse(BaseModel):
    """
    Agent response model.
    Contains the answer, metadata, and optional sources.
    """

    answer: str
    metadata: dict[str, Any] | None = None
    sources: list[dict[str, Any]] | None = None
    conversation_id: str | None = None

    model_config = ConfigDict(extra="allow")
