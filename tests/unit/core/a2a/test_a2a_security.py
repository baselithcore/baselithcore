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
    NONCE_HEADER,
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
            BODY,
            headers[TIMESTAMP_HEADER],
            headers[SIGNATURE_HEADER],
            SECRET,
            nonce_header=headers[NONCE_HEADER],
        )

    def test_nonce_replay_rejected(self) -> None:
        """A captured signed request must not verify twice (single-use nonce)."""
        headers = build_signature_headers(BODY, SECRET)
        kwargs = dict(nonce_header=headers[NONCE_HEADER])
        assert verify_signature(
            BODY, headers[TIMESTAMP_HEADER], headers[SIGNATURE_HEADER], SECRET, **kwargs
        )
        assert not verify_signature(
            BODY, headers[TIMESTAMP_HEADER], headers[SIGNATURE_HEADER], SECRET, **kwargs
        )

    def test_nonce_cannot_be_stripped(self) -> None:
        """Dropping the nonce header must invalidate the MAC (no downgrade)."""
        headers = build_signature_headers(BODY, SECRET)
        assert not verify_signature(
            BODY, headers[TIMESTAMP_HEADER], headers[SIGNATURE_HEADER], SECRET
        )

    def test_nonceless_request_rejected_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A valid legacy MAC without a nonce is replayable for the whole skew
        window, so nonce-less requests are refused unless the operator has
        explicitly opted into the legacy compatibility window."""
        import time as _time

        from core.a2a.security import _compute_signature

        monkeypatch.delenv("BASELITH_A2A_ALLOW_LEGACY_NONCELESS", raising=False)
        ts = str(int(_time.time()))
        legacy_sig = _compute_signature(BODY, ts, SECRET.get_secret_value())
        assert not verify_signature(BODY, ts, legacy_sig, SECRET)

    def test_legacy_peer_without_nonce_verifies_only_with_optin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Old peers sign without a nonce; the explicit opt-in keeps a staged
        rollout possible, with the previous window-bounded replay exposure."""
        import time as _time

        from core.a2a.security import _compute_signature

        monkeypatch.setenv("BASELITH_A2A_ALLOW_LEGACY_NONCELESS", "true")
        ts = str(int(_time.time()))
        legacy_sig = _compute_signature(BODY, ts, SECRET.get_secret_value())
        for _ in range(2):  # window-bounded exposure unchanged for legacy peers
            assert verify_signature(BODY, ts, legacy_sig, SECRET)

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


class _FakeSyncRedis:
    """Minimal sync stand-in: SET NX EX semantics."""

    def __init__(self, fail: bool = False):
        self.store: dict[str, bytes] = {}
        self.fail = fail

    def set(self, key, value, nx=False, ex=None):
        if self.fail:
            raise ConnectionError("redis down")
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True


class TestRedisNonceLedger:
    """Cross-replica single-use: the per-process ledger let a captured signed
    request replay once PER REPLICA inside the skew window."""

    def test_redis_ledger_rejects_second_use(self) -> None:
        from core.a2a.security import _NonceLedger, _RedisNonceLedger

        ledger = _RedisNonceLedger(_FakeSyncRedis(), fallback=_NonceLedger())
        assert ledger.register_once("n1", ttl_seconds=10) is True
        assert ledger.register_once("n1", ttl_seconds=10) is False

    def test_redis_error_falls_back_to_process_ledger(self) -> None:
        """A2A availability beats cross-replica strictness: Redis loss
        degrades to the documented per-process posture, never to open."""
        from core.a2a.security import _NonceLedger, _RedisNonceLedger

        ledger = _RedisNonceLedger(_FakeSyncRedis(fail=True), fallback=_NonceLedger())
        assert ledger.register_once("n1", ttl_seconds=10) is True
        assert ledger.register_once("n1", ttl_seconds=10) is False  # fallback holds


class TestPerPeerSecrets:
    """Per-peer identity: with one mesh-wide secret, any compromised peer
    could impersonate every other peer. A peer that declares X-A2A-Peer signs
    with ITS OWN secret and binds the peer id inside the MAC; the verifier
    resolves the secret from BASELITH_A2A_PEER_SECRETS."""

    def _sign_as(self, monkeypatch, peer_id: str, secret: str) -> dict[str, str]:
        monkeypatch.setenv("BASELITH_A2A_PEER_ID", peer_id)
        monkeypatch.setenv("BASELITH_A2A_SHARED_SECRET", secret)
        return build_signature_headers(BODY, SecretStr(secret))

    def _verify(self, monkeypatch, headers: dict[str, str], peer_secrets: str) -> bool:
        from core.a2a.security import PEER_HEADER

        monkeypatch.setenv("BASELITH_A2A_PEER_SECRETS", peer_secrets)
        return verify_signature(
            BODY,
            headers[TIMESTAMP_HEADER],
            headers[SIGNATURE_HEADER],
            None,
            nonce_header=headers[NONCE_HEADER],
            peer_header=headers.get(PEER_HEADER),
        )

    def test_peer_bound_roundtrip(self, monkeypatch) -> None:
        from core.a2a.security import PEER_HEADER

        headers = self._sign_as(monkeypatch, "alpha", "secret-alpha-0123456789")
        assert headers[PEER_HEADER] == "alpha"
        assert self._verify(
            monkeypatch, headers, "alpha=secret-alpha-0123456789,beta=secret-beta"
        )

    def test_peer_header_swap_rejected(self, monkeypatch) -> None:
        """The peer id is bound INSIDE the MAC: relabeling a captured request
        as another peer must fail even if the attacker knows both ids."""
        from core.a2a.security import PEER_HEADER

        headers = self._sign_as(monkeypatch, "alpha", "secret-alpha-0123456789")
        headers[PEER_HEADER] = "beta"
        assert not self._verify(
            monkeypatch,
            headers,
            "alpha=secret-alpha-0123456789,beta=secret-alpha-0123456789",
        )

    def test_unknown_peer_rejected(self, monkeypatch) -> None:
        headers = self._sign_as(monkeypatch, "ghost", "secret-ghost-0123456789")
        assert not self._verify(monkeypatch, headers, "alpha=secret-alpha")

    def test_legacy_path_without_peer_header_still_works(self, monkeypatch) -> None:
        monkeypatch.delenv("BASELITH_A2A_PEER_ID", raising=False)
        monkeypatch.setenv("BASELITH_A2A_PEER_SECRETS", "alpha=secret-alpha")
        headers = build_signature_headers(BODY, SECRET)
        assert verify_signature(
            BODY,
            headers[TIMESTAMP_HEADER],
            headers[SIGNATURE_HEADER],
            SECRET,
            nonce_header=headers[NONCE_HEADER],
            peer_header=None,
        )

    def test_invalid_peer_id_never_signed(self, monkeypatch) -> None:
        """Peer ids are [A-Za-z0-9_-]{1,64}: a dot would create framing
        ambiguity inside the MAC message."""
        monkeypatch.setenv("BASELITH_A2A_PEER_ID", "bad.peer")
        monkeypatch.setenv("BASELITH_A2A_SHARED_SECRET", "s" * 20)
        from core.a2a.security import PEER_HEADER

        headers = build_signature_headers(BODY, SecretStr("s" * 20))
        assert PEER_HEADER not in headers


class TestPeerSecretParsingNeverLogsMaterial:
    """A malformed BASELITH_A2A_PEER_SECRETS entry must not leak its content.

    ``entry.partition("=")`` puts the WHOLE entry in the peer slot when no
    separator is present, so an operator who set the variable to a bare
    secret would have had it partially logged.
    """

    def _parse(self, monkeypatch, records: list, value: str) -> dict:
        """Parse ``value``, capturing every warning the module emits."""
        from core.a2a import security

        class _Recorder:
            def warning(self, message, *args, **kwargs):
                records.append(message % args if args else message)

            def __getattr__(self, _name):
                return lambda *a, **k: None

        monkeypatch.setenv("BASELITH_A2A_PEER_SECRETS", value)
        monkeypatch.setattr(security, "logger", _Recorder())
        return security.get_a2a_peer_secrets()

    def test_bare_secret_entry_is_not_disclosed(self, monkeypatch) -> None:
        records: list[str] = []
        secret = "sup3rs3cr3tmaterial-do-not-log"
        assert self._parse(monkeypatch, records, secret) == {}
        logged = "\n".join(records)
        assert secret[:16] not in logged
        assert "position=1" in logged

    def test_position_identifies_the_offending_entry(self, monkeypatch) -> None:
        records: list[str] = []
        self._parse(monkeypatch, records, "alpha=a,,bravo=b,broken-third-entry")
        # Empty slots are skipped but still consume an ordinal, so the number
        # points at the comma-separated position an operator would count.
        assert "position=4" in "\n".join(records)

    def test_invalid_peer_id_logged_sanitized(self, monkeypatch) -> None:
        records: list[str] = []
        assert self._parse(monkeypatch, records, "bad peer!=material") == {}
        logged = "\n".join(records)
        assert "a2a_peer_secret_invalid_peer_id" in logged
        assert "material" not in logged

    def test_valid_entries_still_parse(self, monkeypatch) -> None:
        records: list[str] = []
        parsed = self._parse(monkeypatch, records, "alpha=secret-a,bravo=secret-b")
        assert set(parsed) == {"alpha", "bravo"}
        assert parsed["alpha"].get_secret_value() == "secret-a"
        assert records == []
