"""Request and response models of the workflow API.

Split from :mod:`baselith.plugins.api_routers.routes.workflows` for the module size
cap. They are plain Pydantic models; the OpenAPI schema is generated from them.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from baselith.core.workflows.generator import MAX_REQUEST_CHARS

__all__ = [
    "WorkflowDraft",
    "WorkflowDraftRequest",
    "WorkflowPage",
    "WorkflowRemoved",
    "WorkflowRunPage",
    "WorkflowRunRequest",
    "WorkflowRunView",
    "WorkflowSaved",
    "WorkflowSummary",
    "WorkflowValidation",
]


class WorkflowSummary(BaseModel):
    """One catalog entry, without its graph."""

    id: str
    name: str
    description: str
    version: str
    nodes: int
    schedule: str | None
    webhook_trigger: bool
    mcp_exposed: bool


class WorkflowPage(BaseModel):
    """A page of catalog entries."""

    workflows: list[WorkflowSummary]
    count: int
    next_cursor: str | None
    has_more: bool


class WorkflowSaved(BaseModel):
    """Result of saving a definition."""

    id: str
    persisted: bool = Field(description="Written to the catalog directory")


class WorkflowValidation(BaseModel):
    """Result of validating a definition without saving it."""

    valid: bool
    errors: list[str]


class WorkflowRemoved(BaseModel):
    """Result of deleting a definition."""

    id: str
    removed: bool


class WorkflowRunRequest(BaseModel):
    """Start a run."""

    input: Any = Field(default=None, description="Initial input of the workflow")
    wait: bool = Field(
        default=True,
        description="Wait for the run to finish (or pause); false answers 202 at once",
    )


class WorkflowRunView(BaseModel):
    """The state of one run."""

    run_id: str
    workflow_id: str
    source: str
    status: str = Field(
        description="running | completed | failed | timed_out | awaiting_approval"
    )
    started_at: float
    finished_at: float | None
    output: Any = None
    error: str | None


class WorkflowRunPage(BaseModel):
    """Recent runs, newest first."""

    runs: list[WorkflowRunView]
    count: int


class WorkflowDraftRequest(BaseModel):
    """What to draft."""

    description: str = Field(
        min_length=1,
        max_length=MAX_REQUEST_CHARS,
        description="What the workflow should do (or, with revise_id, what to change)",
    )
    revise_id: str | None = Field(
        default=None, max_length=100, description="Catalog workflow to revise instead"
    )
    max_attempts: int = Field(default=3, ge=1, le=5)


class WorkflowDraft(BaseModel):
    """A validated draft. It is not saved."""

    attempts: int
    warnings: list[str]
    yaml: str
    workflow: dict[str, Any]
