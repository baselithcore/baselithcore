"""Response models for the ``api_routers`` HTTP surface.

These models exist for the OpenAPI document: before them most routes returned
a bare ``dict``, so every 2xx schema was an untyped object and every generated
client read ``Any``. They are **descriptive, not prescriptive** — each one
reproduces the JSON payload its route already sent:

- Keys are declared in the order the route builds them, so the serialized
  body keeps its key order.
- Open-ended payloads (tracker records, index status, run state) allow extra
  keys (``extra="allow"``), so a key the model does not name still ships.
- Numbers that may arrive as either ``int`` or ``float`` are typed
  :data:`Number` (``int | float``): a plain ``float`` field would turn ``5``
  into ``5.0`` on the wire.
- Where a key is only present sometimes, the field has a default and the route
  sets ``response_model_exclude_unset=True``, so an absent key stays absent
  instead of appearing as ``null``.
- A route whose model carries open values (``Any`` fields or extra keys fed
  from run state, agent results or provider exports) passes its payload
  through :func:`fastapi.encoders.jsonable_encoder` before returning it.
  Pydantic serializes such values differently from the encoder the routes
  used before they had models (``Decimal`` as a string instead of a number,
  a UTC ``datetime`` with ``Z`` instead of ``+00:00``) and raises on an
  arbitrary object the encoder would have turned into a dict; pre-encoding
  leaves only JSON-native values, which the model passes through unchanged.

:class:`ProblemDetails` documents the RFC 9457 error body every non-2xx
response carries (:mod:`core.api.errors`); :func:`problem_responses` builds
the ``responses=`` mapping a route declares for its error statuses. It is
documentation only — the error handlers render the body.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from core.api.errors import PROBLEM_JSON_MEDIA_TYPE
from core.webhooks.types import WebhookDelivery

#: A JSON number whose int/float spelling is preserved on the wire.
Number = int | float

_CURSOR_DOC = (
    "Opaque cursor for the next page (pass back as ``cursor``); null at the end"
)


class _Open(BaseModel):
    """Base for payloads that may carry keys the model does not name."""

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------- errors


class ProblemDetails(_Open):
    """RFC 9457 problem document (``application/problem+json``).

    ``errors`` (validation failures) and other allowlisted extension members
    appear only on the errors that carry them.
    """

    type: str = Field(
        description="Stable machine classifier, urn:baselith:error:<code>"
    )
    title: str
    status: int
    detail: Any = Field(default=None, description="Human-readable explanation")
    code: str = Field(description="Stable error code")
    request_id: str | None = Field(default=None, description="Correlation id")
    instance: str | None = Field(default=None, description="The request path")
    errors: list[Any] | None = Field(
        default=None, description="Per-field validation errors (422 only)"
    )


def problem_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """The ``responses=`` entries documenting ``status_codes`` as problem+json.

    Args:
        status_codes: The error statuses the route can answer with.

    Returns:
        A mapping FastAPI merges into the operation's documented responses.
    """
    schema = {"$ref": "#/components/schemas/ProblemDetails"}
    return {
        code: {
            "model": ProblemDetails,
            "content": {PROBLEM_JSON_MEDIA_TYPE: {"schema": schema}},
        }
        for code in status_codes
    }


# ------------------------------------------------------------------ feedback


class FeedbackReceived(BaseModel):
    """The sanitized feedback record as stored."""

    query: str
    answer: str
    feedback: str
    conversation_id: str | None
    sources: list[dict[str, Any]] | None
    comment: str | None = Field(default=None, description="Present only when given")


class FeedbackAck(BaseModel):
    """``POST /feedback`` acknowledgement."""

    status: str
    received: FeedbackReceived


# ---------------------------------------------------------------- async runs


class AsyncRunAccepted(BaseModel):
    """``POST /agent/async`` — the queued run and where to poll it."""

    task_id: str
    status_url: str


class AsyncRunStatus(_Open):
    """``GET /agent/status/{task_id}`` — the task tracker record.

    ``result`` is present once the run produced one, ``error`` once it failed.
    """

    status: str | None = Field(
        default=None,
        description="queued|running|retrying|completed|failed|cancelled — retrying is not terminal",
    )
    progress: Number | None = None
    message: str | None = None
    updated_at: str | None = Field(default=None, description="ISO-8601 timestamp")
    result: Any = None
    error: str | None = None
    tenant_id: str | None = None


# --------------------------------------------------------------------- index


class IndexStatus(_Open):
    """``GET /index/status`` — the background indexing engine's state."""

    bootstrapped: bool | None = None
    running: bool | None = None
    mode: str | None = None
    error: str | None = None
    last_completed: str | None = None
    last_new_documents: int | None = None
    bootstrap_enabled: bool
    state: str = Field(description="running | idle")


class IndexRunAccepted(BaseModel):
    """``POST /reindex`` (``202 Accepted``) — poll ``status_url``."""

    status: str = Field(description="Always ``scheduled``")
    mode: str
    status_url: str


class IndexBootstrapAccepted(_Open):
    """``POST /index/bootstrap`` (``202 Accepted``).

    The engine's status fields, then the scheduling receipt.
    """

    bootstrapped: bool | None = None
    running: bool | None = None
    mode: str
    error: str | None = None
    last_completed: str | None = None
    last_new_documents: int | None = None
    status: str = Field(description="Always ``scheduled``")
    status_url: str


# ------------------------------------------------------------------- prompts


class PromptSummary(BaseModel):
    """One prompt in the catalog."""

    name: str
    versions: list[str]
    labels: dict[str, str] = Field(description="label -> version")


class PromptPage(BaseModel):
    """``GET /prompts`` — one page of the catalog."""

    prompts: list[PromptSummary]
    count: int = Field(description="Prompts on this page")
    next_cursor: str | None = Field(description=_CURSOR_DOC)
    has_more: bool
    total: int = Field(description="Every prompt, across pages")


class PromptVersionRegistered(BaseModel):
    """``POST /prompts/{name}/versions``."""

    name: str
    version: str
    checksum: str


class PromptLabelPromoted(BaseModel):
    """``POST /prompts/{name}/labels/{label}``."""

    name: str
    label: str
    version: str


# ------------------------------------------------------------------- privacy


class PrivacyProviders(BaseModel):
    """``GET /privacy/providers``."""

    providers: list[str]


class ErasureResult(BaseModel):
    """``POST /privacy/erase`` — per-provider erased counts.

    A provider that failed is listed in ``failed`` and absent from ``erased``.
    """

    subject_id: str
    completed_at: Number
    erased: dict[str, int]
    failed: list[str]
    total: int


class RetentionResult(BaseModel):
    """``POST /privacy/retention/sweep`` — per-provider purged counts."""

    older_than_seconds: int
    completed_at: Number
    purged: dict[str, int]
    total: int


# ---------------------------------------------------------------------- runs


class RunSnapshotSummary(_Open):
    """One recorded checkpoint snapshot of a run."""

    version: int
    status: str
    step: int
    updated_at: Number


class RunHistoryPage(BaseModel):
    """``GET /runs/{run_id}/history`` — version-ascending snapshots."""

    run_id: str
    history: list[RunSnapshotSummary]
    count: int = Field(description="Snapshots on this page")
    next_cursor: str | None = Field(description=_CURSOR_DOC)
    has_more: bool


class RunState(_Open):
    """``GET /runs/{run_id}/history/{version}`` — the full checkpoint state."""

    run_id: str
    tenant_id: str | None
    query: str
    intent: str | None
    status: str
    step: int
    budget: dict[str, Any]
    trajectory: list[Any]
    plugin_data: dict[str, Any]
    answer: Any
    error: str | None
    steps: dict[str, Any] = Field(
        description="step_key -> {tool_name, args, result, at}"
    )
    pending_approval: dict[str, Any] | None
    version: int
    created_at: Number
    updated_at: Number


class RunForked(BaseModel):
    """``POST /runs/{run_id}/fork`` — the fresh, resumable fork."""

    source_run_id: str
    source_version: int
    run_id: str
    status: str
    steps: int = Field(description="Recorded steps carried into the fork")


# ----------------------------------------------------------------- approvals


class PendingApproval(BaseModel):
    """A run durably paused ``awaiting_approval``."""

    run_id: str
    tenant_id: str | None
    query: str
    intent: str | None
    pending_approval: dict[str, Any] = Field(
        description="The tool/category awaiting review"
    )
    updated_at: Number


class PendingApprovalPage(BaseModel):
    """``GET /approvals`` — newest first, keyset-paginated."""

    pending: list[PendingApproval]
    count: int = Field(description="Runs on this page")
    next_cursor: str | None = Field(description=_CURSOR_DOC)
    has_more: bool


class ApprovalRecorded(BaseModel):
    """``POST /approvals/{run_id}/decision``."""

    run_id: str
    recorded: bool
    approved: bool


class RunResumed(BaseModel):
    """``POST /approvals/{run_id}/resume`` — the resumed run's outcome."""

    run_id: str
    result: Any = Field(description="Whatever the agent loop returned")


# ------------------------------------------------------------------ webhooks


class WebhookEndpointView(_Open):
    """A webhook subscription as the API shows it (signing secret removed)."""

    id: str
    tenant_id: str
    url: str
    event_types: list[str]
    enabled: bool
    description: str | None
    headers: dict[str, str]
    created_at: Number
    has_secret: bool


class WebhookEndpointPage(BaseModel):
    """``GET /webhooks`` — the tenant's endpoints, one page."""

    endpoints: list[WebhookEndpointView]
    count: int = Field(description="Endpoints on this page")
    next_cursor: str | None = Field(description=_CURSOR_DOC)
    has_more: bool


class WebhookDeleted(BaseModel):
    """``DELETE /webhooks/{endpoint_id}``."""

    status: str = Field(description="Always ``deleted``")
    endpoint_id: str


class WebhookDeliveryPage(BaseModel):
    """``GET /webhooks/deliveries`` — the tenant's delivery records, one page."""

    deliveries: list[WebhookDelivery]
    count: int = Field(description="Deliveries on this page")
    next_cursor: str | None = Field(description=_CURSOR_DOC)
    has_more: bool


class WebhookReplayed(BaseModel):
    """``POST /webhooks/deliveries/{delivery_id}/replay``."""

    status: str = Field(description="The replayed delivery's outcome")
    delivery: WebhookDelivery


__all__ = [
    "ApprovalRecorded",
    "AsyncRunAccepted",
    "AsyncRunStatus",
    "ErasureResult",
    "FeedbackAck",
    "FeedbackReceived",
    "IndexBootstrapAccepted",
    "IndexRunAccepted",
    "IndexStatus",
    "Number",
    "PendingApproval",
    "PendingApprovalPage",
    "PrivacyProviders",
    "ProblemDetails",
    "PromptLabelPromoted",
    "PromptPage",
    "PromptSummary",
    "PromptVersionRegistered",
    "RetentionResult",
    "RunForked",
    "RunHistoryPage",
    "RunResumed",
    "RunSnapshotSummary",
    "RunState",
    "WebhookDeleted",
    "WebhookDeliveryPage",
    "WebhookEndpointPage",
    "WebhookEndpointView",
    "WebhookReplayed",
    "problem_responses",
]
