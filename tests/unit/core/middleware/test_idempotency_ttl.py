"""Replay TTL is bounded by the credential that earned the stored response.

Replay runs before route auth and is keyed on the raw credential, so without
a cap a revoked or expired bearer token would keep replaying cached 2xx
responses for the full (24h) idempotency TTL.
"""

from __future__ import annotations

import base64
import time
from types import SimpleNamespace
from unittest.mock import patch

import orjson
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.middleware.idempotency import IdempotencyMiddleware, _jwt_exp


class _RecordingRedis:
    """Minimal async Redis double that remembers the ``ex`` of every SET."""

    def __init__(self) -> None:
        self.store: dict[str, object] = {}
        self.expiries: dict[str, int | None] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        self.expiries[key] = ex
        return True

    async def delete(self, key):
        self.store.pop(key, None)
        self.expiries.pop(key, None)
        return 1


def _jwt(claims: dict) -> str:
    def b64(obj: dict) -> str:
        return base64.urlsafe_b64encode(orjson.dumps(obj)).decode().rstrip("=")

    return f"{b64({'alg': 'HS256', 'typ': 'JWT'})}.{b64(claims)}.sig"


def _client(fake: _RecordingRedis, *, ttl_seconds: int = 86_400) -> TestClient:
    app = FastAPI()

    @app.post("/do")
    def _do():
        return {"ok": True}

    with patch("core.middleware.idempotency.create_redis_client", return_value=fake):
        mw = IdempotencyMiddleware(app, ttl_seconds=ttl_seconds)
    return TestClient(mw, raise_server_exceptions=False)


def _stored_ex(fake: _RecordingRedis) -> int | None:
    entries = {k: v for k, v in fake.expiries.items() if not k.endswith(":lock")}
    assert len(entries) == 1, entries
    return next(iter(entries.values()))


def test_jwt_exp_reads_numeric_claim_and_tolerates_garbage():
    assert _jwt_exp(_jwt({"exp": 1234})) == 1234.0
    assert _jwt_exp(_jwt({"sub": "x"})) is None
    assert _jwt_exp("not.a.jwt.at.all") is None
    assert _jwt_exp("opaque-token") is None


def test_bearer_jwt_replay_expires_with_the_token():
    fake = _RecordingRedis()
    client = _client(fake)
    token = _jwt({"exp": int(time.time()) + 120})
    r = client.post(
        "/do", headers={"Idempotency-Key": "k1", "Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200
    ex = _stored_ex(fake)
    assert ex is not None and 0 < ex <= 120


def test_opaque_bearer_replay_capped_at_access_token_lifetime():
    fake = _RecordingRedis()
    client = _client(fake)
    with patch(
        "core.config.get_security_config",
        return_value=SimpleNamespace(access_token_lifetime=900),
    ):
        r = client.post(
            "/do", headers={"Idempotency-Key": "k2", "Authorization": "Bearer opaque"}
        )
    assert r.status_code == 200
    assert _stored_ex(fake) == 900


def test_api_key_replay_keeps_configured_ttl():
    fake = _RecordingRedis()
    client = _client(fake, ttl_seconds=4321)
    r = client.post("/do", headers={"Idempotency-Key": "k3", "X-API-Key": "key"})
    assert r.status_code == 200
    assert _stored_ex(fake) == 4321


def test_short_ttl_wins_over_a_long_lived_token():
    fake = _RecordingRedis()
    client = _client(fake, ttl_seconds=60)
    token = _jwt({"exp": int(time.time()) + 10_000})
    with patch(
        "core.config.get_security_config",
        return_value=SimpleNamespace(access_token_lifetime=3600),
    ):
        client.post(
            "/do", headers={"Idempotency-Key": "k4", "Authorization": f"Bearer {token}"}
        )
    assert _stored_ex(fake) == 60
