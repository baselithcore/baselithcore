"""
Chat Models.

Pydantic models for chat requests, responses, and feedback.
"""

from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict


class ChatRequest(BaseModel):
    """
    Request model for the agent.
    Contains the user query and, optionally, the conversation_id.
    The `stream` flag is accepted for compatibility, but the dedicated
    streaming endpoint is `/chat/stream`.
    """

    query: str
    conversation_id: Optional[str] = None
    stream: Optional[bool] = False
    rag_only: bool = False
    kb_label: Optional[str] = None
    tenant_id: Optional[str] = None

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

    document_id: Optional[str] = None
    title: Optional[str] = None
    path: Optional[str] = None
    url: Optional[str] = None
    origin: Optional[str] = None
    source_type: Optional[Literal["path", "url"]] = None
    score: Optional[float] = None

    model_config = ConfigDict(extra="allow")


class FeedbackRequest(BaseModel):
    """
    Model for recording feedback on a generated response.
    - query: the question asked
    - answer: the answer provided by the bot
    - feedback: positive or negative rating
    - conversation_id: widget session (if present)
    - sources: references to the documents used in the response
    """

    query: str
    answer: str
    feedback: Literal["positive", "negative"]
    conversation_id: Optional[str] = None
    sources: Optional[List[Union[FeedbackDocumentReference, Dict[str, Any]]]] = None
    comment: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class ChatResponse(BaseModel):
    """
    Agent response model.
    Contains the answer, metadata, and optional sources.
    """

    answer: str
    metadata: Optional[Dict[str, Any]] = None
    sources: Optional[List[Dict[str, Any]]] = None
    conversation_id: Optional[str] = None

    model_config = ConfigDict(extra="allow")
