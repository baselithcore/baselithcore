"""ConnectorHttp: status mapping, retry, circuit breaker, egress, redaction."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from core.connectors import (
    ConnectorAuthError,
    ConnectorConnectError,
    ConnectorEgressError,
    ConnectorHttp,
    ConnectorNotFoundError,
    ConnectorRateLimitedError,
    ConnectorRequestError,
    ConnectorTransientError,
    ConnectorUnavailableError,
)
from core.connectors.errors import parse_retry_after
from core.resilience import get_circuit_breaker
from core.security.http import create_hardened_async_client


def _http(spec, rec, **kwargs) -> ConnectorHttp:
    return ConnectorHttp(spec, transport=httpx.MockTransport(rec), **kwargs)


async def test_success_returns_response(spec, recorder):
    rec = recorder(httpx.Response(200, json={"ok": True}))
    http = _http(spec, rec)
    resp = await http.request("GET", "https://api.acme.test/v1/ping")
    assert resp.json() == {"ok": True}
    await http.aclose()


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, ConnectorAuthError),
        (403, ConnectorAuthError),
        (404, ConnectorNotFoundError),
        (422, ConnectorRequestError),
    ],
)
async def test_client_errors_map_to_permanent_errors_without_retry(
    spec, recorder, status, error
):
    rec = recorder(httpx.Response(status, text="nope"))
    http = _http(spec, rec)
    with pytest.raises(error) as info:
        await http.request("GET", "https://api.acme.test/x")
    assert len(rec.requests) == 1
    assert info.value.connector == spec.name
    await http.aclose()


async def test_request_error_carries_status(spec, recorder):
    http = _http(spec, recorder(httpx.Response(409, text="conflict")))
    with pytest.raises(ConnectorRequestError) as info:
        await http.request("POST", "https://api.acme.test/x")
    assert info.value.status == 409
    await http.aclose()


async def test_server_errors_are_retried_then_raise_transient(spec, recorder):
    rec = recorder(httpx.Response(503, text="down"))
    http = _http(spec, rec)
    with pytest.raises(ConnectorTransientError):
        await http.request("GET", "https://api.acme.test/x")
    assert len(rec.requests) == spec.max_attempts
    await http.aclose()


async def test_transient_failure_then_success_recovers(spec, recorder):
    rec = recorder(
        httpx.ConnectError("boom"),
        httpx.Response(502),
        httpx.Response(200, json={"n": 1}),
    )
    http = _http(spec, rec)
    resp = await http.request("GET", "https://api.acme.test/x")
    assert resp.json() == {"n": 1}
    assert len(rec.requests) == 3
    await http.aclose()


async def test_rate_limit_is_retried_and_carries_retry_after(spec, recorder):
    rec = recorder(httpx.Response(429, headers={"Retry-After": "7"}))
    http = _http(replace(spec, max_attempts=1), rec)
    with pytest.raises(ConnectorRateLimitedError) as info:
        await http.request("GET", "https://api.acme.test/x")
    assert info.value.retry_after == 7.0
    assert isinstance(info.value, ConnectorTransientError)
    await http.aclose()


async def test_rate_limit_then_success(spec, recorder):
    rec = recorder(httpx.Response(429), httpx.Response(200, text="ok"))
    http = _http(spec, rec)
    resp = await http.request("GET", "https://api.acme.test/x")
    assert resp.text == "ok"
    await http.aclose()


def test_parse_retry_after_accepts_seconds_and_http_dates():
    assert parse_retry_after("12") == 12.0
    assert parse_retry_after(None) is None
    assert parse_retry_after("garbage") is None
    future = datetime.now(UTC) + timedelta(seconds=30)
    parsed = parse_retry_after(format_datetime(future, usegmt=True))
    assert parsed is not None and 25 <= parsed <= 31


async def test_outage_opens_the_breaker(spec, recorder):
    rec = recorder(httpx.Response(500))
    http = _http(replace(spec, max_attempts=1), rec)
    breaker = get_circuit_breaker(f"connector.{spec.name}")
    for _ in range(breaker.fail_max):
        with pytest.raises(ConnectorTransientError):
            await http.request("GET", "https://api.acme.test/x")
    sent = len(rec.requests)
    with pytest.raises(ConnectorUnavailableError):
        await http.request("GET", "https://api.acme.test/x")
    assert len(rec.requests) == sent  # the open circuit short-circuits the call
    await http.aclose()


async def test_client_errors_never_open_the_breaker(spec, recorder):
    rec = recorder(httpx.Response(404))
    http = _http(spec, rec)
    breaker = get_circuit_breaker(f"connector.{spec.name}")
    for _ in range(breaker.fail_max + 2):
        with pytest.raises(ConnectorNotFoundError):
            await http.request("GET", "https://api.acme.test/x")
    assert breaker.state.value == "closed"
    await http.aclose()


async def test_host_outside_allowlist_is_an_egress_error(spec, recorder):
    rec = recorder(httpx.Response(200))
    http = _http(spec, rec)
    with pytest.raises(ConnectorEgressError):
        await http.request("GET", "https://evil.test/x")
    assert rec.requests == []
    await http.aclose()


async def test_internal_address_is_an_egress_error(spec, recorder):
    rec = recorder(httpx.Response(200))
    http = _http(spec, rec)
    with pytest.raises(ConnectorEgressError):
        await http.request("GET", "https://internal.acme.test/x")
    await http.aclose()


async def test_secrets_are_redacted_from_error_messages(spec, recorder):
    token = "sk-live-SUPERSECRET"
    rec = recorder(httpx.Response(400, text=f"bad key {token} for account"))
    http = _http(spec, rec, secrets=[token])
    with pytest.raises(ConnectorRequestError) as info:
        await http.request("GET", "https://api.acme.test/x")
    assert token not in str(info.value)
    assert "***" in str(info.value)
    await http.aclose()


async def test_quoted_body_is_truncated(spec, recorder):
    http = _http(spec, recorder(httpx.Response(400, text="x" * 5000)))
    with pytest.raises(ConnectorRequestError) as info:
        await http.request("GET", "https://api.acme.test/x")
    assert len(str(info.value)) < 400
    await http.aclose()


async def test_injected_client_is_not_closed(spec, recorder):
    client = create_hardened_async_client(
        transport=httpx.MockTransport(recorder(httpx.Response(200)))
    )
    http = ConnectorHttp(spec, client=client)
    await http.request("GET", "https://api.acme.test/x")
    await http.aclose()
    assert not client.is_closed
    await client.aclose()


async def test_an_unguarded_injected_client_is_refused(spec, recorder):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(recorder(httpx.Response(200)))
    )
    with pytest.raises(ValueError, match="SSRF"):
        ConnectorHttp(spec, client=client)
    await client.aclose()


async def test_owned_client_is_closed(spec, recorder):
    http = _http(spec, recorder(httpx.Response(200)))
    await http.request("GET", "https://api.acme.test/x")
    client = http.client
    await http.aclose()
    assert client.is_closed


async def test_post_is_not_retried_after_a_server_error(spec, recorder):
    rec = recorder(httpx.Response(503), httpx.Response(201))
    http = _http(spec, rec)
    with pytest.raises(ConnectorTransientError):
        await http.request("POST", "https://api.acme.test/tickets")
    assert len(rec.requests) == 1  # the first POST may have been applied
    await http.aclose()


async def test_post_is_not_retried_after_a_read_timeout(spec, recorder):
    rec = recorder(httpx.ReadTimeout("slow"), httpx.Response(201))
    http = _http(spec, rec)
    with pytest.raises(ConnectorTransientError):
        await http.request("POST", "https://api.acme.test/tickets")
    assert len(rec.requests) == 1
    await http.aclose()


async def test_post_is_retried_when_the_connection_never_opened(spec, recorder):
    rec = recorder(httpx.ConnectError("refused"), httpx.Response(201))
    http = _http(spec, rec)
    resp = await http.request("POST", "https://api.acme.test/tickets")
    assert resp.status_code == 201
    assert len(rec.requests) == 2
    await http.aclose()


async def test_post_is_retried_after_a_rate_limit(spec, recorder):
    rec = recorder(httpx.Response(429), httpx.Response(201))
    http = _http(spec, rec)
    resp = await http.request("POST", "https://api.acme.test/tickets")
    assert resp.status_code == 201
    await http.aclose()


async def test_caller_can_declare_a_post_idempotent(spec, recorder):
    rec = recorder(httpx.Response(503), httpx.Response(200))
    http = _http(spec, rec)
    resp = await http.request("POST", "https://api.acme.test/search", idempotent=True)
    assert resp.status_code == 200
    assert len(rec.requests) == 2
    await http.aclose()


async def test_connect_failure_is_a_connect_error(spec, recorder):
    http = _http(replace(spec, max_attempts=1), recorder(httpx.ConnectError("x")))
    with pytest.raises(ConnectorConnectError):
        await http.request("GET", "https://api.acme.test/x")
    await http.aclose()
