"""The feedback-store retries must be scoped to transient DB errors.

`@retry` defaults to retrying *every* Exception; on `get_feedback_analytics`
(a six-query fan-out) a programming error or integrity violation used to
re-run the whole fan-out three times with backoff, holding pool connections
for ~2s of pure sleep before failing anyway. Only connection-level errors
(OperationalError/InterfaceError) are worth a second attempt.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import patch

import psycopg
import pytest

from core.db import feedback as feedback_mod


def _failing_connection(exc: Exception, counter: list[int]):
    @asynccontextmanager
    async def _conn():
        counter[0] += 1
        raise exc
        yield  # pragma: no cover

    return _conn


async def test_programming_error_is_not_retried() -> None:
    calls = [0]
    with patch.object(
        feedback_mod,
        "get_async_connection",
        _failing_connection(psycopg.ProgrammingError("bad sql"), calls),
    ):
        with pytest.raises(psycopg.ProgrammingError):
            await feedback_mod.get_feedbacks()
    assert calls[0] == 1  # no useless re-runs of a deterministic failure


async def test_operational_error_is_retried() -> None:
    calls = [0]
    with patch.object(
        feedback_mod,
        "get_async_connection",
        _failing_connection(psycopg.OperationalError("connection lost"), calls),
    ):
        with pytest.raises(psycopg.OperationalError):
            await feedback_mod.get_feedbacks()
    assert calls[0] == 3  # transient: worth the max_attempts
