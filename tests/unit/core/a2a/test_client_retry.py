"""Retry classification for the A2A client.

Regression: the invoke loop used to retry every exception — including
deterministic 4xx responses on a non-idempotent POST — with no jitter.
"""

from __future__ import annotations

import httpx
import pytest

from core.a2a.client import _is_retryable_error


def _status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://peer/a2a/invoke")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


@pytest.mark.parametrize("status", [500, 502, 503, 504, 408, 429])
def test_transient_statuses_are_retryable(status):
    assert _is_retryable_error(_status_error(status)) is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422])
def test_deterministic_4xx_is_not_retryable(status):
    assert _is_retryable_error(_status_error(status)) is False


def test_transport_and_timeout_errors_are_retryable():
    assert _is_retryable_error(httpx.ConnectError("refused")) is True
    assert _is_retryable_error(httpx.ReadTimeout("slow")) is True
    assert _is_retryable_error(TimeoutError()) is True


def test_local_errors_are_not_retryable():
    # A serialization bug fails identically on every attempt.
    assert _is_retryable_error(TypeError("not JSON serializable")) is False
    assert _is_retryable_error(ValueError("bad payload")) is False
