"""Keyed (HMAC-SHA256) audit hash chain.

An unkeyed SHA-256 chain proves nothing against an attacker with write access
to the database: recompute every link and the trail verifies again. A keyed
chain makes forgery require the key as well as the file.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from core.config.audit import AuditConfig, reset_audit_config
from core.observability.audit import AuditEvent, AuditEventType, reset_audit_logger
from core.observability.audit_chain import (
    GENESIS_HASH,
    AuditChainKeyError,
    SQLiteAuditSink,
    compute_entry_hash,
    require_chain_key_from_env,
)

pytestmark = [pytest.mark.unit]

_KEY = SecretStr("super-secret-chain-key")


@pytest.fixture(autouse=True)
def _clean_audit_config():
    """Reset both globals this module touches.

    ``configure_audit_logging`` installs a process-wide ``AuditLogger``; left
    behind, it holds a sink whose SQLite connection this module has closed and
    whose file lives in a torn-down ``tmp_path``, so an unrelated later test
    that emits an audit event would write into a dead handle.
    """
    reset_audit_config()
    reset_audit_logger()
    yield
    reset_audit_config()
    reset_audit_logger()


def _config(monkeypatch, **kwargs):
    """Patch the audit config the sink resolves its key from."""
    monkeypatch.setattr(
        "core.observability.audit_chain.get_audit_config",
        lambda: AuditConfig(**kwargs),
    )


def _event(action: str = "login") -> AuditEvent:
    return AuditEvent(
        event_type=AuditEventType.AUTH_LOGIN,
        user_id="u1",
        tenant_id="t1",
        action=action,
        success=True,
    )


class TestComputeEntryHash:
    def test_unkeyed_is_the_historical_sha256(self):
        payload = {"x": 1}
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), default=str
        )
        expected = hashlib.sha256((GENESIS_HASH + canonical).encode()).hexdigest()
        assert compute_entry_hash(GENESIS_HASH, payload) == expected

    def test_keyed_is_hmac_sha256(self):
        payload = {"x": 1}
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), default=str
        )
        expected = hmac.new(
            b"k", (GENESIS_HASH + canonical).encode(), hashlib.sha256
        ).hexdigest()
        assert compute_entry_hash(GENESIS_HASH, payload, key=b"k") == expected

    def test_keyed_differs_from_unkeyed(self):
        payload = {"x": 1}
        assert compute_entry_hash(GENESIS_HASH, payload) != compute_entry_hash(
            GENESIS_HASH, payload, key=b"k"
        )

    def test_a_different_key_yields_a_different_digest(self):
        payload = {"x": 1}
        assert compute_entry_hash(
            GENESIS_HASH, payload, key=b"a"
        ) != compute_entry_hash(GENESIS_HASH, payload, key=b"b")

    def test_secretstr_key_is_accepted(self):
        payload = {"x": 1}
        assert compute_entry_hash(
            GENESIS_HASH, payload, key=SecretStr("k")
        ) == compute_entry_hash(GENESIS_HASH, payload, key=b"k")


@pytest.mark.asyncio
class TestKeyedSink:
    async def test_chain_verifies_with_the_key(self, tmp_path, monkeypatch):
        _config(monkeypatch, AUDIT_CHAIN_HMAC_KEY=_KEY)
        sink = SQLiteAuditSink(tmp_path / "a.db")
        await sink.write(_event())
        await sink.write(_event("logout"))
        try:
            assert sink.verify_chain().ok is True
            assert sink.count() == 2
        finally:
            sink.close()

    async def test_rows_are_not_plain_sha256(self, tmp_path, monkeypatch):
        _config(monkeypatch, AUDIT_CHAIN_HMAC_KEY=_KEY)
        sink = SQLiteAuditSink(tmp_path / "b.db")
        await sink.write(_event())
        try:
            row = sink.query()[0]
            payload = {
                k: row[k]
                for k in (
                    "event_id",
                    "timestamp",
                    "event_type",
                    "user_id",
                    "tenant_id",
                    "session_id",
                    "resource",
                    "action",
                    "details",
                    "success",
                    "ip_address",
                )
            }
            assert row["entry_hash"] != compute_entry_hash(row["prev_hash"], payload)
            assert row["entry_hash"] == compute_entry_hash(
                row["prev_hash"], payload, key=_KEY
            )
        finally:
            sink.close()

    async def test_verification_fails_under_a_rotated_key(self, tmp_path, monkeypatch):
        _config(monkeypatch, AUDIT_CHAIN_HMAC_KEY=_KEY)
        sink = SQLiteAuditSink(tmp_path / "c.db")
        await sink.write(_event())
        sink.close()

        _config(monkeypatch, AUDIT_CHAIN_HMAC_KEY=SecretStr("a-different-key"))
        reopened = SQLiteAuditSink(tmp_path / "c.db")
        try:
            result = reopened.verify_chain()
            assert result.ok is False
            assert "AUDIT_CHAIN_HMAC_KEY" in (result.reason or "")
        finally:
            reopened.close()

    async def test_explicit_key_argument_wins_over_config(self, tmp_path, monkeypatch):
        _config(monkeypatch, AUDIT_CHAIN_HMAC_KEY=_KEY)
        sink = SQLiteAuditSink(tmp_path / "d.db", hmac_key=SecretStr("explicit"))
        await sink.write(_event())
        try:
            assert sink.verify_chain().ok is True
            row = sink.query()[0]
            assert row["entry_hash"] == compute_entry_hash(
                row["prev_hash"],
                {
                    k: row[k]
                    for k in (
                        "event_id",
                        "timestamp",
                        "event_type",
                        "user_id",
                        "tenant_id",
                        "session_id",
                        "resource",
                        "action",
                        "details",
                        "success",
                        "ip_address",
                    )
                },
                key=b"explicit",
            )
        finally:
            sink.close()


class TestUnkeyedFallback:
    def test_warns_when_no_key_is_configured(self, tmp_path, monkeypatch):
        _config(monkeypatch)
        warnings: list[str] = []
        monkeypatch.setattr(
            "core.observability.audit_chain.logger.warning",
            lambda msg, *a, **k: warnings.append(str(msg)),
        )
        SQLiteAuditSink(tmp_path / "e.db").close()
        assert any("HMAC" in message for message in warnings)

    def test_no_warning_when_the_chain_is_disabled(self, tmp_path, monkeypatch):
        _config(monkeypatch)
        warnings: list[str] = []
        monkeypatch.setattr(
            "core.observability.audit_chain.logger.warning",
            lambda msg, *a, **k: warnings.append(str(msg)),
        )
        SQLiteAuditSink(tmp_path / "f.db", hash_chain=False).close()
        assert warnings == []

    def test_require_key_refuses_to_build_an_unkeyed_sink(self, tmp_path, monkeypatch):
        _config(monkeypatch, AUDIT_CHAIN_REQUIRE_KEY=True)
        with pytest.raises(AuditChainKeyError):
            SQLiteAuditSink(tmp_path / "g.db")

    def test_require_key_is_satisfied_by_a_key(self, tmp_path, monkeypatch):
        _config(monkeypatch, AUDIT_CHAIN_REQUIRE_KEY=True, AUDIT_CHAIN_HMAC_KEY=_KEY)
        sink = SQLiteAuditSink(tmp_path / "h.db")
        sink.close()


def _require_key_env(monkeypatch, tmp_path, key: str | None) -> None:
    """Put the real environment into the require-key state.

    Not a patched config object: the whole point is that ``AuditConfig``
    *refuses to build* in this state, and the guard has to survive that.
    """
    monkeypatch.setenv("AUDIT_ENABLED", "true")
    monkeypatch.setenv("AUDIT_CHAIN_REQUIRE_KEY", "true")
    monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "audit.db"))
    if key is None:
        monkeypatch.delenv("AUDIT_CHAIN_HMAC_KEY", raising=False)
    else:
        monkeypatch.setenv("AUDIT_CHAIN_HMAC_KEY", key)
    reset_audit_config()


class TestRequireKeyFailsClosed:
    """``AUDIT_CHAIN_REQUIRE_KEY`` must survive the configuration it breaks.

    ``AuditConfig`` raises a ``ValidationError`` in exactly the require-key /
    no-key state, so any handler that swallows it hands back the unkeyed chain
    the operator forbade — and, in the app, a silently disabled audit trail.
    """

    def test_env_probe_reads_the_variable_without_pydantic(self, monkeypatch):
        monkeypatch.setenv("AUDIT_CHAIN_REQUIRE_KEY", "TRUE")
        assert require_chain_key_from_env() is True
        monkeypatch.setenv("AUDIT_CHAIN_REQUIRE_KEY", "false")
        assert require_chain_key_from_env() is False
        monkeypatch.delenv("AUDIT_CHAIN_REQUIRE_KEY", raising=False)
        assert require_chain_key_from_env() is False

    def test_sink_refuses_when_the_config_itself_is_invalid(
        self, tmp_path, monkeypatch
    ):
        _require_key_env(monkeypatch, tmp_path, key=None)
        with pytest.raises(AuditChainKeyError):
            SQLiteAuditSink(tmp_path / "audit.db")

    def test_sink_never_falls_back_to_an_unkeyed_chain(self, tmp_path, monkeypatch):
        """The regression: a swallowed ValidationError opened the chain anyway."""
        _require_key_env(monkeypatch, tmp_path, key=None)
        try:
            sink = SQLiteAuditSink(tmp_path / "audit.db")
        except AuditChainKeyError:
            return
        sink.close()
        pytest.fail("an unkeyed chain was opened despite AUDIT_CHAIN_REQUIRE_KEY")

    def test_configure_audit_logging_refuses_to_boot(self, tmp_path, monkeypatch):
        from core.observability.audit_setup import configure_audit_logging

        _require_key_env(monkeypatch, tmp_path, key=None)
        with pytest.raises(AuditChainKeyError):
            configure_audit_logging()

    def test_start_audit_trail_refuses_to_boot(self, tmp_path, monkeypatch):
        from core.observability.audit_setup import start_audit_trail

        _require_key_env(monkeypatch, tmp_path, key=None)
        app = SimpleNamespace(state=SimpleNamespace())
        with pytest.raises(AuditChainKeyError):
            start_audit_trail(app)

    def test_with_a_key_the_trail_boots_and_the_chain_is_keyed(
        self, tmp_path, monkeypatch
    ):
        from core.observability.audit_setup import (
            configure_audit_logging,
            get_durable_audit_sink,
        )

        _require_key_env(monkeypatch, tmp_path, key="a-real-chain-key")
        audit_logger = configure_audit_logging()
        try:
            assert audit_logger is not None
            sink = get_durable_audit_sink()
            assert sink is not None
            assert sink._hmac_key == b"a-real-chain-key"
        finally:
            if (sink := get_durable_audit_sink()) is not None:
                sink.close()

    def test_an_unrelated_config_failure_still_degrades_gracefully(
        self, tmp_path, monkeypatch
    ):
        """Only the require-key case stops the boot; nothing else changes."""
        monkeypatch.delenv("AUDIT_CHAIN_REQUIRE_KEY", raising=False)
        monkeypatch.setattr(
            "core.observability.audit_chain.get_audit_config",
            lambda: (_ for _ in ()).throw(RuntimeError("config backend down")),
        )
        sink = SQLiteAuditSink(tmp_path / "degraded.db")
        try:
            assert sink._hmac_key is None  # unkeyed, with the startup warning
        finally:
            sink.close()


class TestConfigValidation:
    def test_require_key_without_a_key_is_rejected_at_boot(self):
        with pytest.raises(ValueError, match="AUDIT_CHAIN_HMAC_KEY"):
            AuditConfig(
                AUDIT_ENABLED=True,
                AUDIT_CHAIN_REQUIRE_KEY=True,
                AUDIT_DB_PATH="/tmp/x.db",
            )

    def test_require_key_with_a_key_validates(self):
        config = AuditConfig(
            AUDIT_ENABLED=True,
            AUDIT_CHAIN_REQUIRE_KEY=True,
            AUDIT_CHAIN_HMAC_KEY=_KEY,
        )
        assert config.chain_hmac_key is not None

    def test_key_is_a_secret(self):
        config = AuditConfig(AUDIT_CHAIN_HMAC_KEY="literal-key")
        assert isinstance(config.chain_hmac_key, SecretStr)
        assert "literal-key" not in repr(config)

    def test_disabled_audit_does_not_trip_the_requirement(self):
        config = AuditConfig(AUDIT_CHAIN_REQUIRE_KEY=True)
        assert config.enabled is False

    @pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
    def test_a_blank_key_is_no_key_to_the_config(self, blank):
        """Both guards must agree. The config used to accept a blank key
        (``is None`` only), while the sink's coercion refused it — so a
        deployment could believe it had a keyed chain that it did not."""
        assert AuditConfig(AUDIT_CHAIN_HMAC_KEY=blank).chain_hmac_key is None

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_require_key_rejects_a_blank_key(self, blank):
        with pytest.raises(ValueError, match="AUDIT_CHAIN_HMAC_KEY"):
            AuditConfig(
                AUDIT_ENABLED=True,
                AUDIT_CHAIN_REQUIRE_KEY=True,
                AUDIT_CHAIN_HMAC_KEY=blank,
            )

    def test_a_key_that_is_only_whitespace_never_keys_the_chain(self, tmp_path):
        """Three spaces is not a key; using it would look configured."""
        sink = SQLiteAuditSink(tmp_path / "ws.db", hmac_key="   ", hash_chain=False)
        try:
            assert sink._hmac_key is None
        finally:
            sink.close()

    def test_meaningful_surrounding_whitespace_is_preserved(self, tmp_path):
        """Stripping a real key would silently invalidate an existing chain."""
        sink = SQLiteAuditSink(tmp_path / "pad.db", hmac_key=" real ")
        try:
            assert sink._hmac_key == b" real "
        finally:
            sink.close()
