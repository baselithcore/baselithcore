"""Tests for the structlog sensitive-data redaction processor.

The redaction previously lived in a stdlib filter that (a) was only installed on
the MCP path and (b) saw neither structlog kwargs nor JSON-rendered fields.
These tests pin the processor's by-key and by-value behavior and that it honors
``log_masking_enabled``.
"""

from __future__ import annotations

import pytest

from core.observability import logging as logmod
from core.observability.logging import redact_sensitive, redact_url_credentials


@pytest.fixture
def masking_on(monkeypatch):
    class _Cfg:
        log_masking_enabled = True

    monkeypatch.setattr(logmod, "get_app_config", lambda: _Cfg())


def _run(event_dict):
    return redact_sensitive(None, "info", event_dict)


def test_redacts_secret_by_key(masking_on):
    out = _run({"event": "auth", "token": "abc123", "api_key": "sk-secret"})
    assert out["token"] == "[REDACTED]"
    assert out["api_key"] == "[REDACTED]"


def test_redacts_nested_dict_keys(masking_on):
    out = _run(
        {
            "event": "req",
            "headers": {"Authorization": "Bearer xyz", "X-Api-Key": "k"},
        }
    )
    assert out["headers"]["Authorization"] == "[REDACTED]"
    assert out["headers"]["X-Api-Key"] == "[REDACTED]"


def test_masks_email_in_message(masking_on):
    out = _run({"event": "user alice@example.com logged in"})
    assert "alice@example.com" not in out["event"]
    assert "@example.com" in out["event"]


def test_masks_inline_credentials_in_message(masking_on):
    out = _run({"event": "connecting with password=hunter2 now"})
    assert "hunter2" not in out["event"]
    assert "[REDACTED]" in out["event"]


def test_non_sensitive_fields_untouched(masking_on):
    out = _run({"event": "ok", "user_id": "u-1", "count": 5})
    assert out["user_id"] == "u-1"
    assert out["count"] == 5


def test_disabled_when_masking_off(monkeypatch):
    class _Cfg:
        log_masking_enabled = False

    monkeypatch.setattr(logmod, "get_app_config", lambda: _Cfg())
    out = _run({"event": "x", "token": "abc123"})
    assert out["token"] == "abc123"  # untouched when disabled


def test_redact_url_credentials_strips_password():
    assert (
        redact_url_credentials("redis://:sekret@cache.host:6379/2")
        == "redis://cache.host:6379/2"
    )
    assert (
        redact_url_credentials("postgres://user:pw@db.host:5432/app")
        == "postgres://db.host:5432/app"
    )


def test_redact_url_credentials_noop_without_userinfo():
    assert (
        redact_url_credentials("redis://cache.host:6379/1")
        == "redis://cache.host:6379/1"
    )


def test_installed_in_configure_logging_pipeline():
    # Both the structlog chain and the foreign pre-chain must include the
    # redaction processor so the FastAPI/uvicorn path is covered.
    import inspect

    src = inspect.getsource(logmod.configure_logging)
    assert src.count("redact_sensitive") >= 2
