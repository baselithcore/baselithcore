"""A provider that echoes a credential never leaks it into logs or exception text."""

from __future__ import annotations

import logging

import httpx
import pytest

from core.connectors import ConnectorHttp
from core.connectors.errors import (
    ConnectorError,
    error_for_response,
    redact,
)
from core.observability.redaction import redact_secrets, safe_snippet

BEARER = "ya29.BEARER-TOKEN-ECHOED-0123456789"
REFRESH = "1//REFRESH-TOKEN-ECHOED-abcdef"
CODE = "4/0AUTH-CODE-ECHOED-zzz"
CLIENT_SECRET = "CLIENT-SECRET-ECHOED-qqq"
SECRETS = (BEARER, REFRESH, CODE, CLIENT_SECRET)


def _echo_body() -> str:
    return (
        f'{{"error": "invalid_request", "echo": "Authorization: Bearer {BEARER}", '
        f'"refresh_token": "{REFRESH}", "code": "{CODE}", '
        f'"client_secret":"{CLIENT_SECRET}"}}'
    )


def _assert_clean(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 503])
def test_error_messages_never_carry_an_echoed_credential(status):
    request = httpx.Request("GET", "https://api.acme.test/x")
    response = httpx.Response(status, text=_echo_body(), request=request)
    error = error_for_response("acme", response)
    assert isinstance(error, ConnectorError)
    _assert_clean(str(error))
    assert f"HTTP {status}" in str(error)


@pytest.mark.parametrize("status", [400, 500])
async def test_retry_logs_and_final_exception_never_carry_it(
    spec, recorder, caplog, status
):
    caplog.set_level(logging.DEBUG)
    rec = recorder(httpx.Response(status, text=_echo_body()))
    http = ConnectorHttp(spec, transport=httpx.MockTransport(rec))
    with pytest.raises(ConnectorError) as info:
        await http.request(
            "GET",
            "https://api.acme.test/x",
            headers={"Authorization": f"Bearer {BEARER}"},
        )
    await http.aclose()
    _assert_clean(str(info.value))
    _assert_clean(repr(info.value))
    _assert_clean(caplog.text)
    for record in caplog.records:
        _assert_clean(record.getMessage())
    if status == 500:  # the retry path really logged something to scan
        assert any(
            "Retry" in r.getMessage() or "Attempt" in r.getMessage()
            for r in caplog.records
        )


async def test_request_headers_are_never_embedded(spec, recorder, caplog):
    caplog.set_level(logging.DEBUG)
    rec = recorder(httpx.Response(500, text="boom"))
    http = ConnectorHttp(spec, transport=httpx.MockTransport(rec))
    with pytest.raises(ConnectorError) as info:
        await http.request(
            "GET",
            "https://api.acme.test/x",
            headers={"Authorization": f"Bearer {BEARER}"},
        )
    await http.aclose()
    assert BEARER not in str(info.value) + caplog.text


def test_the_snippet_is_bounded_single_line_and_redacted_before_the_cut():
    body = "x" * 190 + f' access_token="{BEARER}"\n\r' + "y" * 5000
    snippet = safe_snippet(body, limit=200)
    assert len(snippet) <= 200 and "\n" not in snippet and "\r" not in snippet
    assert BEARER not in snippet and BEARER[:10] not in snippet


@pytest.mark.parametrize(
    "text",
    [
        f"Authorization: Bearer {BEARER}",
        f"bearer {BEARER}",
        f'{{"access_token": "{BEARER}"}}',
        f"{{'refresh_token': '{REFRESH}'}}",
        f"refresh_token={REFRESH}&grant_type=refresh_token",
        f"https://api.acme.test/cb?code={CODE}&state=s",
        f'"code": "{CODE}"',
        f"client_secret={CLIENT_SECRET}",
        f'{{"client_secret": "{CLIENT_SECRET}"}}',
        "token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.c2lnbmF0dXJl",  # gitleaks:allow (fake fixture)
    ],
)
def test_credential_shapes_are_masked(text):
    out = redact_secrets(text)
    for secret in (
        BEARER,
        REFRESH,
        CODE,
        CLIENT_SECRET,
        "eyJhbGciOiJIUzI1NiJ9",
    ):  # gitleaks:allow (fake fixture)
        assert secret not in out
    assert "[REDACTED]" in out


def test_explicit_secrets_are_masked_anywhere():
    assert redact("see sk-live-XYZ here", ["sk-live-XYZ"]) == "see *** here"


def test_status_codes_and_plain_prose_survive():
    text = '{"code": 403, "message": "Daily Limit Exceeded", "reason": "dailyLimitExceeded"}'
    assert redact(text, []) == text


async def test_the_requests_own_bearer_is_redacted_when_echoed_raw(
    spec, recorder, caplog
):
    """A provider echoing the raw token (no ``Bearer`` marker) is still masked."""
    token = "opaque-token-VALUE-0123456789"
    caplog.set_level(logging.DEBUG)
    rec = recorder(httpx.Response(500, text=f"boom {token}"))
    http = ConnectorHttp(spec, transport=httpx.MockTransport(rec))
    with pytest.raises(ConnectorError) as info:
        await http.request(
            "GET",
            "https://api.acme.test/x",
            headers={"Authorization": f"Bearer {token}"},
        )
    await http.aclose()
    assert token not in str(info.value) + caplog.text
    assert "boom ***" in str(info.value)


def test_keyword_runs_do_not_backtrack_quadratically():
    """Bounded affixes: a long run of keyword-like text stays linear."""
    import time

    small = "token" * 800
    large = "token" * 8000
    start = time.perf_counter()
    redact_secrets(small)
    t_small = time.perf_counter() - start
    start = time.perf_counter()
    redact_secrets(large)
    t_large = time.perf_counter() - start
    # 10x the input: linear is ~10x, the old quadratic pattern was ~100x.
    assert t_large < max(t_small, 0.001) * 40


@pytest.mark.parametrize(
    "text",
    [
        "x" * 60 + "_access_token=SUPERSECRET",
        '{"my_' + "a" * 300 + '_api_key": "SUPERSECRET"}',
        "status=token=SUPERSECRET",
        "a=b=c=d=api_key=SUPERSECRET",
    ],
)
def test_no_key_shape_skips_redaction(text):
    """A long key prefix or a non-secret pair in front never hides a secret."""
    assert "SUPERSECRET" not in redact_secrets(text)


def test_non_credential_pairs_and_numeric_codes_are_kept():
    assert redact_secrets("codex=fine code=404") == "codex=fine code=404"
