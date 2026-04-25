"""Tests for the Sentry ``before_send`` scrubber."""

from __future__ import annotations

from typing import Any

from core.observability.sentry import _before_send


def _scrub(payload: dict[str, Any]) -> dict[str, Any]:
    return _before_send(payload, {})


def test_scrubs_sensitive_request_headers() -> None:
    event = {
        "request": {
            "headers": {
                "Authorization": "Bearer ey...secret",
                "Cookie": "session=abc",
                "X-API-Key": "sk-live-xyz",
                "User-Agent": "pytest",
            }
        }
    }
    out = _scrub(event)
    headers = out["request"]["headers"]
    assert headers["Authorization"] == "[REDACTED]"
    assert headers["Cookie"] == "[REDACTED]"
    assert headers["X-API-Key"] == "[REDACTED]"
    assert headers["User-Agent"] == "pytest"


def test_scrubs_password_and_token_keys_in_data() -> None:
    event = {
        "request": {
            "data": {
                "username": "alice",
                "password": "hunter2",
                "api_key": "pk_xxx",
                "session_token": "tok",
                "nested": {"jwt": "abc.def.ghi", "ok": 1},
            }
        }
    }
    out = _scrub(event)
    data = out["request"]["data"]
    assert data["username"] == "alice"
    assert data["password"] == "[REDACTED]"
    assert data["api_key"] == "[REDACTED]"
    assert data["session_token"] == "[REDACTED]"
    assert data["nested"]["jwt"] == "[REDACTED]"
    assert data["nested"]["ok"] == 1


def test_scrubs_extras_and_contexts() -> None:
    event = {
        "extra": {"secret": "x", "harmless": 1},
        "contexts": {"runtime": {"version": "3.12", "TOKEN": "leak"}},
    }
    out = _scrub(event)
    assert out["extra"]["secret"] == "[REDACTED]"
    assert out["extra"]["harmless"] == 1
    assert out["contexts"]["runtime"]["TOKEN"] == "[REDACTED]"
    assert out["contexts"]["runtime"]["version"] == "3.12"


def test_scrubs_exception_frame_locals() -> None:
    event = {
        "exception": {
            "values": [
                {
                    "stacktrace": {
                        "frames": [
                            {
                                "vars": {
                                    "self": "<obj>",
                                    "password": "leak",
                                    "config_token": "leak",
                                    "x": 42,
                                }
                            }
                        ]
                    }
                }
            ]
        }
    }
    out = _scrub(event)
    frame_vars = out["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]
    assert frame_vars["password"] == "[REDACTED]"
    assert frame_vars["config_token"] == "[REDACTED]"
    assert frame_vars["self"] == "<obj>"
    assert frame_vars["x"] == 42


def test_scrubs_thread_frame_vars() -> None:
    event = {
        "threads": {
            "values": [
                {"stacktrace": {"frames": [{"vars": {"jwt_token": "xyz", "n": 1}}]}}
            ]
        }
    }
    out = _scrub(event)
    frame_vars = out["threads"]["values"][0]["stacktrace"]["frames"][0]["vars"]
    assert frame_vars["jwt_token"] == "[REDACTED]"
    assert frame_vars["n"] == 1


def test_handles_missing_or_empty_payload() -> None:
    assert _scrub({}) == {}
    assert _scrub({"request": {}}) == {"request": {}}
