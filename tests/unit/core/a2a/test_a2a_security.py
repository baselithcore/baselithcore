"""Tests for A2A HMAC request signing (core.a2a.security)."""

import time

import pytest
from pydantic import SecretStr

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core.a2a.agent_card import AgentCard  # noqa: E402
from core.a2a.router import create_a2a_router  # noqa: E402
from core.a2a.security import (  # noqa: E402
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    build_signature_headers,
    get_a2a_shared_secret,
    verify_signature,
)
from core.a2a.server import EchoA2AServer  # noqa: E402

SECRET = SecretStr("mesh-shared-secret-with-enough-entropy")
BODY = b'{"jsonrpc": "2.0", "method": "message/send", "id": "1"}'


class TestSignVerify:
    def test_roundtrip(self) -> None:
        headers = build_signature_headers(BODY, SECRET)
        assert verify_signature(
            BODY, headers[TIMESTAMP_HEADER], headers[SIGNATURE_HEADER], SECRET
        )

    def test_tampered_body_rejected(self) -> None:
        headers = build_signature_headers(BODY, SECRET)
        assert not verify_signature(
            BODY + b"x", headers[TIMESTAMP_HEADER], headers[SIGNATURE_HEADER], SECRET
        )

    def test_wrong_secret_rejected(self) -> None:
        headers = build_signature_headers(BODY, SECRET)
        assert not verify_signature(
            BODY,
            headers[TIMESTAMP_HEADER],
            headers[SIGNATURE_HEADER],
            SecretStr("other-secret"),
        )

    def test_missing_headers_rejected(self) -> None:
        assert not verify_signature(BODY, None, None, SECRET)
        headers = build_signature_headers(BODY, SECRET)
        assert not verify_signature(BODY, headers[TIMESTAMP_HEADER], None, SECRET)
        assert not verify_signature(BODY, None, headers[SIGNATURE_HEADER], SECRET)

    def test_stale_timestamp_rejected(self) -> None:
        headers = build_signature_headers(BODY, SECRET)
        old_ts = str(int(time.time()) - 3600)
        # Re-sign with the old timestamp so only freshness fails... actually a
        # naive replay keeps the original signature with a swapped timestamp,
        # which must fail BOTH the MAC and the window.
        assert not verify_signature(BODY, old_ts, headers[SIGNATURE_HEADER], SECRET)

    def test_garbage_timestamp_rejected(self) -> None:
        headers = build_signature_headers(BODY, SECRET)
        assert not verify_signature(
            BODY, "not-a-number", headers[SIGNATURE_HEADER], SECRET
        )

    def test_secret_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("BASELITH_A2A_SHARED_SECRET", raising=False)
        assert get_a2a_shared_secret() is None
        monkeypatch.setenv("BASELITH_A2A_SHARED_SECRET", "s3cret")
        secret = get_a2a_shared_secret()
        assert secret is not None
        assert secret.get_secret_value() == "s3cret"


class TestRouterEnforcement:
    def _client(self) -> TestClient:
        card = AgentCard(name="echo", description="echo agent")
        app = FastAPI()
        app.include_router(create_a2a_router(EchoA2AServer(card)))
        return TestClient(app)

    def _payload(self) -> bytes:
        return (
            b'{"jsonrpc": "2.0", "method": "message/send", "id": "1", '
            b'"params": {"message": {"role": "user", '
            b'"parts": [{"kind": "text", "text": "hi"}], "messageId": "m1"}}}'
        )

    def test_unsigned_allowed_without_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BASELITH_A2A_SHARED_SECRET", raising=False)
        resp = self._client().post(
            "/a2a",
            content=self._payload(),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert "result" in resp.json()

    def test_unsigned_rejected_in_production_without_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Fail closed: in production, an unsigned request with no configured
        # secret and no explicit opt-in must be rejected.
        monkeypatch.delenv("BASELITH_A2A_SHARED_SECRET", raising=False)
        monkeypatch.delenv("BASELITH_A2A_ALLOW_UNAUTHENTICATED", raising=False)
        monkeypatch.setenv("APP_ENV", "production")
        resp = self._client().post(
            "/a2a",
            content=self._payload(),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 401

    def test_unsigned_allowed_in_production_with_optin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("BASELITH_A2A_SHARED_SECRET", raising=False)
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("BASELITH_A2A_ALLOW_UNAUTHENTICATED", "true")
        resp = self._client().post(
            "/a2a",
            content=self._payload(),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert "result" in resp.json()

    def test_unsigned_rejected_with_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BASELITH_A2A_SHARED_SECRET", "mesh-secret")
        resp = self._client().post(
            "/a2a",
            content=self._payload(),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == -32001

    def test_signed_accepted_with_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BASELITH_A2A_SHARED_SECRET", "mesh-secret")
        body = self._payload()
        headers = build_signature_headers(body, SecretStr("mesh-secret"))
        headers["Content-Type"] = "application/json"
        resp = self._client().post("/a2a", content=body, headers=headers)
        assert resp.status_code == 200
        assert "result" in resp.json()

    def test_bad_signature_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BASELITH_A2A_SHARED_SECRET", "mesh-secret")
        body = self._payload()
        headers = build_signature_headers(body, SecretStr("wrong-secret"))
        headers["Content-Type"] = "application/json"
        resp = self._client().post("/a2a", content=body, headers=headers)
        assert resp.status_code == 401
