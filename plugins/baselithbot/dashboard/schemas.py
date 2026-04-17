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


class ProviderKeyRequest(BaseModel):
    api_key: str = Field(..., min_length=8, max_length=512)


class ChannelConfigRequest(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)
    unset_fields: list[str] = Field(default_factory=list)


class ChannelTestRequest(BaseModel):
    target: str = Field(..., min_length=1, max_length=512)
    text: str = Field(default="Baselithbot test message", max_length=2000)


class ClawHubConfigRequest(BaseModel):
    base_url: str | None = Field(default=None, min_length=1, max_length=512)
    convex_url: str | None = Field(default=None, min_length=1, max_length=512)
    auth_token: str | None = Field(default=None, min_length=1, max_length=512)
    install_dir: str | None = Field(default=None, min_length=1, max_length=512)
    timeout_seconds: float | None = Field(default=None, ge=1.0, le=300.0)


__all__ = [
    "ChannelConfigRequest",
    "ChannelTestRequest",
    "ClawHubConfigRequest",
    "CronIntervalRequest",
    "CronToggleRequest",
    "PairingTokenRequest",
    "ProviderKeyRequest",
    "SessionCreateRequest",
    "SessionSendRequest",
]
