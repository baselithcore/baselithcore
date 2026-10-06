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


# --- Async agent runs (`/agent/async`, `/agent/status/{task_id}`) ---

#: Task states after which a queued run's status no longer changes.
TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled"})

#: ``AgentEvent`` types after which the server closes a run's event stream.
TERMINAL_EVENT_TYPES = frozenset({"final", "error", "human"})


class AgentRunRequest(BaseModel):
    """Submission payload for an async agent run (`POST /agent/async`)."""

    query: str = Field(..., min_length=1, max_length=8000)
    conversation_id: str | None = None

    model_config = ConfigDict(extra="forbid")


class AgentRunSubmission(BaseModel):
    """`202 Accepted` for a queued run: the id to poll and where to poll it.

    ``location`` is the response's ``Location`` header (the same URL as
    ``status_url``).
    """

    task_id: str
    status_url: str
    location: str | None = None

    model_config = ConfigDict(extra="allow")


class AgentRunStatus(BaseModel):
    """The task tracker record for a queued run (`GET /agent/status/{task_id}`).

    ``status`` is one of ``pending``, ``queued``, ``running``, ``completed``,
    ``failed`` or ``cancelled``; ``result`` is set once completed and
    ``error`` once failed.
    """

    status: str
    progress: float | None = None
    message: str | None = None
    updated_at: str | None = None
    result: Any = None
    error: str | None = None

    model_config = ConfigDict(extra="allow")

    @property
    def is_terminal(self) -> bool:
        """Whether the run has stopped changing (completed, failed, cancelled)."""
        return self.status in TERMINAL_RUN_STATUSES


# --- Run events and history (`/runs/...`) ---


class RunEvent(BaseModel):
    """One structured agent event from `GET /runs/{run_id}/events`.

    ``id`` is the SSE ``id:`` (the ``AgentEvent`` id); ``type`` is the SSE
    ``event:`` name (``run_started``, ``thought``, ``tool_call``,
    ``tool_result``, ``memory``, ``human``, ``chunk``, ``final``, ``error``).
    """

    type: str
    id: str | None = None
    content: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    agent_id: str | None = None
    timestamp: str | None = None

    model_config = ConfigDict(extra="allow")

    @property
    def is_terminal(self) -> bool:
        """Whether the server closes the stream after this event."""
        return self.type in TERMINAL_EVENT_TYPES


class Page(BaseModel):
    """Common shape of every cursor-paginated list response.

    Pass ``next_cursor`` back as ``cursor`` to fetch the following page, or
    let ``iter_pages`` do it. ``items`` is the page's list, whatever key the
    endpoint names it under.
    """

    count: int = 0
    next_cursor: str | None = None
    has_more: bool = False

    model_config = ConfigDict(extra="allow")

    @property
    def items(self) -> list[Any]:
        """The entries on this page."""
        return []


class RunHistoryPage(Page):
    """Version-ascending snapshot summaries (`GET /runs/{run_id}/history`)."""

    run_id: str
    history: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def items(self) -> list[Any]:
        return list(self.history)


# --- Approvals (`/approvals`) ---


class PendingApproval(BaseModel):
    """A run durably paused awaiting a reviewer decision."""

    run_id: str
    tenant_id: str | None = None
    query: str | None = None
    intent: str | None = None
    pending_approval: dict[str, Any] = Field(default_factory=dict)
    updated_at: float | None = None

    model_config = ConfigDict(extra="allow")


class ApprovalPage(Page):
    """A page of pending approvals (`GET /approvals`), newest first."""

    pending: list[PendingApproval] = Field(default_factory=list)

    @property
    def items(self) -> list[Any]:
        return list(self.pending)


class ApprovalDecisionResult(BaseModel):
    """Acknowledgement of a recorded decision (`POST /approvals/{run_id}/decision`)."""

    run_id: str
    recorded: bool
    approved: bool

    model_config = ConfigDict(extra="allow")


class RunResumeResult(BaseModel):
    """Outcome of resuming a checkpointed run (`POST /approvals/{run_id}/resume`)."""

    run_id: str
    result: Any = None

    model_config = ConfigDict(extra="allow")


# --- Webhooks (`/webhooks`) ---


class WebhookEndpoint(BaseModel):
    """A webhook subscription as the API returns it (signing secret redacted)."""

    id: str
    url: str
    tenant_id: str | None = None
    event_types: list[str] = Field(default_factory=list)
    enabled: bool = True
    description: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    created_at: float | None = None
    has_secret: bool = True

    model_config = ConfigDict(extra="allow")


class WebhookCreated(BaseModel):
    """A registered endpoint plus its signing ``secret`` — returned only once."""

    endpoint: WebhookEndpoint
    secret: str

    model_config = ConfigDict(extra="allow")


class WebhookPage(Page):
    """A page of webhook endpoints (`GET /webhooks`)."""

    endpoints: list[WebhookEndpoint] = Field(default_factory=list)

    @property
    def items(self) -> list[Any]:
        return list(self.endpoints)


class WebhookDelivery(BaseModel):
    """The record of attempting to deliver one event to one endpoint."""

    id: str
    endpoint_id: str
    event_id: str
    event_type: str
    url: str | None = None
    tenant_id: str | None = None
    status: str = "pending"
    attempts: int = 0
    last_status_code: int | None = None
    last_error: str | None = None
    created_at: float | None = None
    completed_at: float | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")


class WebhookDeliveryPage(Page):
    """A page of delivery records (`GET /webhooks/deliveries`)."""

    deliveries: list[WebhookDelivery] = Field(default_factory=list)

    @property
    def items(self) -> list[Any]:
        return list(self.deliveries)


class WebhookReplay(BaseModel):
    """Result of re-attempting a delivery (`POST /webhooks/deliveries/{id}/replay`)."""

    status: str
    delivery: WebhookDelivery

    model_config = ConfigDict(extra="allow")


class ApprovalDecisionRequest(BaseModel):
    """Reviewer decision (`POST /approvals/{run_id}/decision`).

    ``approver`` is only a display label: the server records the
    authenticated principal as the reviewer.
    """

    approved: bool
    approver: str | None = Field(default=None, max_length=200)
    reason: str | None = Field(default=None, max_length=2000)

    model_config = ConfigDict(extra="forbid")


class WebhookCreateRequest(BaseModel):
    """Subscription payload (`POST /webhooks`); ``["*"]`` subscribes to all."""

    url: str = Field(..., min_length=1, max_length=2048)
    event_types: list[str] = Field(default_factory=lambda: ["*"])
    description: str | None = None
    headers: dict[str, str] | None = None

    model_config = ConfigDict(extra="forbid")
