"""Request models for the Agentic Patterns showcase API."""

from pydantic import BaseModel, Field


class MemoryRequest(BaseModel):
    session_id: str = "default"
    message: str
    store_as_fact: bool = False


class PlanRequest(BaseModel):
    goal: str


class ReflectionRequest(BaseModel):
    output: str
    criteria: list[str] = ["accuracy", "completeness", "clarity"]


class ApprovalActionRequest(BaseModel):
    action: str
    context: dict = {}


class ApprovalDecisionRequest(BaseModel):
    request_id: str
    approved: bool
    reviewer: str = "admin"
    reason: str = ""


class FeedbackRequest(BaseModel):
    query: str
    response: str
    rating: int = Field(..., ge=1, le=5)
    comment: str = ""
