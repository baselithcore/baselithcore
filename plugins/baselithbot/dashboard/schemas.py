"""Pydantic request schemas for the Baselithbot dashboard REST API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SessionCreateRequest(BaseModel):
    title: str = ""
    primary: bool = False


class SessionSendRequest(BaseModel):
    role: str = "user"
    content: str = Field(..., min_length=1, max_length=8000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PairingTokenRequest(BaseModel):
    platform: str | None = None


class CronToggleRequest(BaseModel):
    enabled: bool


class CronIntervalRequest(BaseModel):
    interval_seconds: float = Field(..., ge=1.0, le=86400.0)


class CronActionRequest(BaseModel):
    type: str = Field(..., min_length=1, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)


class CronCustomCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    interval_seconds: float = Field(..., ge=1.0, le=86400.0)
    action: CronActionRequest
    description: str = Field(default="", max_length=500)
    enabled: bool = True


class CronCustomUpdateRequest(BaseModel):
    interval_seconds: float = Field(..., ge=1.0, le=86400.0)
    action: CronActionRequest
    description: str = Field(default="", max_length=500)
    enabled: bool = True


class AgentActionRequest(BaseModel):
    type: str = Field(..., min_length=1, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)


class AgentCustomCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    keywords: list[str] = Field(default_factory=list, max_length=32)
    priority: int = Field(default=100, ge=0, le=10_000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    action: AgentActionRequest


class AgentCustomUpdateRequest(BaseModel):
    description: str = Field(default="", max_length=500)
    keywords: list[str] = Field(default_factory=list, max_length=32)
    priority: int = Field(default=100, ge=0, le=10_000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    action: AgentActionRequest


class AgentDispatchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)
    context: dict[str, Any] = Field(default_factory=dict)


class ProviderKeyRequest(BaseModel):
    api_key: str = Field(..., min_length=8, max_length=512)


class ChannelConfigRequest(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)
    unset_fields: list[str] = Field(default_factory=list)


class ChannelTestRequest(BaseModel):
    target: str = Field(..., min_length=1, max_length=512)
    text: str = Field(default="Baselithbot test message", max_length=2000)


class WorkspaceCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    primary: bool = False
    channel_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkspaceUpdateRequest(BaseModel):
    description: str = Field(default="", max_length=500)
    primary: bool = False
    channel_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CanvasRenderRequest(BaseModel):
    widgets: list[dict[str, Any]] = Field(default_factory=list)
    clear: bool = False


class CanvasDispatchRequest(BaseModel):
    widget_id: str = Field(default="", max_length=200)
    action: str = Field(..., min_length=1, max_length=200)
    payload: dict[str, Any] = Field(default_factory=dict)


class ClawHubConfigRequest(BaseModel):
    base_url: str | None = Field(default=None, min_length=1, max_length=512)
    convex_url: str | None = Field(default=None, min_length=1, max_length=512)
    auth_token: str | None = Field(default=None, min_length=1, max_length=512)
    install_dir: str | None = Field(default=None, min_length=1, max_length=512)
    timeout_seconds: float | None = Field(default=None, ge=1.0, le=300.0)


class WorkspaceSkillCreateRequest(BaseModel):
    slug: str = Field(..., min_length=2, max_length=63)
    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field(..., min_length=1, max_length=500)
    version: str = Field(default="0.1.0", max_length=32)
    instructions: str = Field(..., min_length=1, max_length=32_000)
    surfaces: list[str] = Field(default_factory=lambda: ["chat"], max_length=8)
    tags: list[str] = Field(default_factory=list, max_length=16)
    workspace: str | None = Field(default=None, min_length=1, max_length=120)
    overwrite: bool = False


__all__ = [
    "AgentActionRequest",
    "AgentCustomCreateRequest",
    "AgentCustomUpdateRequest",
    "AgentDispatchRequest",
    "CanvasDispatchRequest",
    "CanvasRenderRequest",
    "ChannelConfigRequest",
    "ChannelTestRequest",
    "ClawHubConfigRequest",
    "CronActionRequest",
    "CronCustomCreateRequest",
    "CronCustomUpdateRequest",
    "CronIntervalRequest",
    "CronToggleRequest",
    "PairingTokenRequest",
    "ProviderKeyRequest",
    "SessionCreateRequest",
    "SessionSendRequest",
    "WorkspaceCreateRequest",
    "WorkspaceSkillCreateRequest",
    "WorkspaceUpdateRequest",
]
