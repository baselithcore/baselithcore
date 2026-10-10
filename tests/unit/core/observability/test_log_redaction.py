"""Tests for the structlog sensitive-data redaction processor.

The redaction previously lived in a stdlib filter that (a) was only installed on
the MCP path and (b) saw neither structlog kwargs nor JSON-rendered fields.
These tests pin the processor's by-key and by-value behavior and that it honors
``log_masking_enabled``.
"""

from __future__ import annotations

import pytest

from baselith.core.observability import logging as logmod
from baselith.core.observability import redaction as redmod
from baselith.core.observability.logging import redact_sensitive, redact_url_credentials


@pytest.fixture
def masking_on(monkeypatch):
    class _Cfg:
        log_masking_enabled = True

    monkeypatch.setattr(redmod, "get_app_config", lambda: _Cfg())


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

    monkeypatch.setattr(redmod, "get_app_config", lambda: _Cfg())
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


def test_redact_url_credentials_fails_closed_on_malformed_url():
    # ``.port`` raises ValueError on an out-of-range port, and urlsplit
    # rejects a broken IPv6 netloc: neither may echo the password.
    for url in (
        "redis://:sekret@cache.host:99999/2",
        "postgres://user:sekret@[::1/app",
    ):
        out = redact_url_credentials(url)
        assert "sekret" not in out
        assert "user" not in out


def test_redact_url_credentials_malformed_keeps_host():
    assert (
        redact_url_credentials("redis://:sekret@cache.host:99999/2")
        == "redis://cache.host:99999/2"
    )


def test_storage_url_redaction_withholds_value_when_redaction_fails(monkeypatch):
    from baselith.core.config import storage
    from baselith.core.observability import redaction

    def boom(_url: str) -> str:
        raise RuntimeError("redaction broke")

    monkeypatch.setattr(redaction, "redact_url_credentials", boom)
    assert storage._redact_url("redis://:sekret@cache.host:6379/0") == "[REDACTED]"


def test_installed_in_configure_logging_pipeline():
    # Both the structlog chain and the foreign pre-chain must include the
    # redaction processor so the FastAPI/uvicorn path is covered.
    import inspect

    src = inspect.getsource(logmod.configure_logging)
    assert src.count("redact_sensitive") >= 2


# --- exact outputs: a redaction that half-works must fail, not just one that stops


@pytest.mark.parametrize(
    ("email", "masked"),
    [
        ("alice@example.com", "ali***@example.com"),
        ("abcd@x.io", "abc***@x.io"),
        ("bob@x.io", "b***@x.io"),
        ("al@x.io", "a***@x.io"),
    ],
)
def test_email_mask_keeps_at_most_three_chars(masking_on, email, masked):
    # Longer than three: the first three survive; three or fewer: only one.
    assert _run({"event": f"user {email} in"})["event"] == f"user {masked} in"


def test_inline_credential_keeps_marker_and_separator(masking_on):
    out = _run({"event": "password=hunter2 then token: abc"})
    assert out["event"] == "password=[REDACTED] then token: [REDACTED]"


@pytest.mark.parametrize(
    ("message", "leak"),
    [
        ("Authorization: Bearer abc.def-123456", "abc.def-123456"),
        ("authorization=Basic dXNlcjpwYXNz", "dXNlcjpwYXNz"),
        ("api_key: Token tk_live_998877", "tk_live_998877"),
    ],
)
def test_auth_scheme_does_not_shield_the_credential(masking_on, message, leak):
    # Regression: the scheme word ("Bearer"/"Basic") was taken as the value,
    # so the credential right after it was logged verbatim.
    out = _run({"event": message})["event"]
    assert leak not in out
    assert out.endswith("[REDACTED]")


def test_nested_values_are_recursed_not_blanked(masking_on):
    out = _run(
        {
            "event": "req",
            "headers": {"Host": "api.local", "Authorization": "x"},
            "items": ["password=pw1", "plain"],
            "pair": ("token=t1", "ok"),
            "by_id": {1: "secret=s1"},
        }
    )
    assert out["headers"] == {"Host": "api.local", "Authorization": "[REDACTED]"}
    assert out["items"] == ["password=[REDACTED]", "plain"]
    assert out["pair"] == ("token=[REDACTED]", "ok")
    assert out["by_id"] == {1: "secret=[REDACTED]"}


def test_masks_when_config_lacks_the_flag(monkeypatch):
    # A config object without ``log_masking_enabled`` keeps masking on.
    monkeypatch.setattr(redmod, "get_app_config", lambda: object())
    assert _run({"event": "x", "token": "abc123"})["token"] == "[REDACTED]"


def test_masks_when_config_is_unreadable(monkeypatch):
    def boom():
        raise RuntimeError("config broke")

    monkeypatch.setattr(redmod, "get_app_config", boom)
    out = _run({"event": "password=pw", "token": "abc123"})
    assert out["token"] == "[REDACTED]"
    assert out["event"] == "password=[REDACTED]"


def test_redact_url_credentials_strips_username_only_token():
    # ``https://<token>@host`` carries a credential with no password part.
    assert (
        redact_url_credentials("https://ghp_tok123@github.com/org/repo.git")
        == "https://github.com/org/repo.git"
    )


@pytest.mark.parametrize(
    "url", ["https://API.Example.COM/v1?q=1#f", "http://[::1]:8080/x"]
)
def test_redact_url_credentials_returns_clean_url_verbatim(url):
    assert redact_url_credentials(url) == url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # a password holding a raw '@': strip up to the *last* one
        ("redis://user:p@ss@cache.host:99999/2", "redis://cache.host:99999/2"),
        # a later '://' (in the query) is not the scheme separator
        (
            "redis://:sekret@cache.host:99999/2?r=http://x",
            "redis://cache.host:99999/2?r=http://x",
        ),
        # an '@' in the path is not userinfo
        ("redis://:s@h:99999/a@b/c", "redis://h:99999/a@b/c"),
        ("redis://h:99999/a@b/c", "redis://h:99999/a@b/c"),
        # scheme-relative still carries userinfo (regression: echoed verbatim)
        ("//u:sekret@h:99999/x", "//h:99999/x"),
    ],
)
def test_redact_url_credentials_malformed_strips_exactly_userinfo(url, expected):
    assert redact_url_credentials(url) == expected


def test_redact_url_credentials_withholds_non_string():
    assert redact_url_credentials(bytearray(b"redis://:pw@h/0")) == "[REDACTED]"
