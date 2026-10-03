"""A database outage answers ``503`` + ``Retry-After``, not ``500 internal_error``.

With PostgreSQL down, ``POST /chat`` used to surface ``PoolTimeout`` through the
catch-all handler: a ``500`` that tells the client nothing about retrying and
pages the on-call engineer for a code defect that does not exist.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import OperationalError
from psycopg_pool import PoolTimeout

from core.api.errors import DATABASE_RETRY_AFTER_SECONDS, install_error_handlers
from core.middleware.unhandled_error import UnhandledErrorMiddleware


def _client(exc: Exception) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.add_middleware(UnhandledErrorMiddleware)

    @app.post("/chat")
    def _chat() -> None:
        raise exc

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    "exc",
    [
        PoolTimeout("couldn't get a connection after 30.00 sec"),
        OperationalError("connection refused: secret-db-host:5432"),
    ],
)
def test_database_outage_is_a_503_problem(exc: Exception) -> None:
    response = _client(exc).post("/chat")
    assert response.status_code == 503
    assert response.headers["retry-after"] == str(DATABASE_RETRY_AFTER_SECONDS)
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["code"] == "service_unavailable"
    assert body["status"] == 503
    # Neither the driver's class nor its message (host names!) reaches the caller.
    assert "secret-db-host" not in response.text
    assert "error_type" not in body


def test_other_failures_stay_500() -> None:
    response = _client(RuntimeError("bug")).post("/chat")
    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
