"""Trust store: key identity, expiry and revocation for plugin signatures.

``BASELITH_PLUGIN_TRUST_ROOTS`` is a comma-separated blob of hex public keys —
it cannot express *which* publisher a key belongs to, when it stops being
valid, or that it was compromised and must stop working today. The trust store
(``BASELITH_PLUGIN_TRUST_STORE=path.json``) adds all three, and merges with the
legacy env var so nothing that worked stops working.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.plugins.signing import (
    TrustedKey,
    enforce_plugin_signature,
    generate_keypair_hex,
    load_trust_roots,
    load_trust_store,
    load_trusted_keys,
    sign_plugin_hash,
)

HASH = "a" * 64


def _write_store(tmp_path: Path, *entries: dict[str, object]) -> Path:
    path = tmp_path / "trust.json"
    path.write_text(json.dumps({"keys": list(entries)}), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "BASELITH_PLUGIN_TRUST_ROOTS",
        "BASELITH_PLUGIN_TRUST_STORE",
        "BASELITH_REQUIRE_PLUGIN_SIGNATURES",
    ):
        monkeypatch.delenv(var, raising=False)


# ── Loading ──────────────────────────────────────────────────────────────────


def test_store_entry_is_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, public = generate_keypair_hex()
    store = _write_store(tmp_path, {"key_id": "publisher-a", "public_key_hex": public})
    monkeypatch.setenv("BASELITH_PLUGIN_TRUST_STORE", str(store))

    keys = load_trust_store()
    assert [k.key_id for k in keys] == ["publisher-a"]
    assert keys[0].public_key_hex == public
    assert keys[0].is_usable is True
    assert load_trust_roots() == [public]


def test_bare_list_store_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, public = generate_keypair_hex()
    path = tmp_path / "trust.json"
    path.write_text(json.dumps([{"public_key_hex": public}]), encoding="utf-8")
    monkeypatch.setenv("BASELITH_PLUGIN_TRUST_STORE", str(path))
    assert load_trust_roots() == [public]


def test_env_roots_still_work_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    _, pub1 = generate_keypair_hex()
    _, pub2 = generate_keypair_hex()
    monkeypatch.setenv("BASELITH_PLUGIN_TRUST_ROOTS", f"{pub1}, {pub2}")
    assert load_trust_roots() == [pub1, pub2]


def test_env_and_store_are_merged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, env_pub = generate_keypair_hex()
    _, store_pub = generate_keypair_hex()
    store = _write_store(tmp_path, {"key_id": "b", "public_key_hex": store_pub})
    monkeypatch.setenv("BASELITH_PLUGIN_TRUST_ROOTS", env_pub)
    monkeypatch.setenv("BASELITH_PLUGIN_TRUST_STORE", str(store))
    assert load_trust_roots() == [env_pub, store_pub]


def test_store_revocation_overrides_a_legacy_env_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Revoking a key must work even while it is still in the env blob."""
    _, public = generate_keypair_hex()
    store = _write_store(
        tmp_path, {"key_id": "leaked", "public_key_hex": public, "revoked": True}
    )
    monkeypatch.setenv("BASELITH_PLUGIN_TRUST_ROOTS", public)
    monkeypatch.setenv("BASELITH_PLUGIN_TRUST_STORE", str(store))
    assert load_trust_roots() == []
    assert [k.key_id for k in load_trusted_keys()] == ["leaked"]


def test_malformed_store_yields_no_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Fail closed: an unreadable store must not silently fall back to trusting."""
    path = tmp_path / "trust.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("BASELITH_PLUGIN_TRUST_STORE", str(path))
    with caplog.at_level(logging.ERROR, logger="core.plugins.signing"):
        assert load_trust_store() == []
    assert "trust store" in caplog.text.lower()


def test_missing_store_file_yields_no_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BASELITH_PLUGIN_TRUST_STORE", str(tmp_path / "absent.json"))
    assert load_trust_store() == []


def test_entry_without_a_usable_key_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, public = generate_keypair_hex()
    store = _write_store(
        tmp_path,
        {"key_id": "bad", "public_key_hex": "zz-not-hex"},
        {"key_id": "short", "public_key_hex": "ab"},
        {"key_id": "good", "public_key_hex": public},
    )
    monkeypatch.setenv("BASELITH_PLUGIN_TRUST_STORE", str(store))
    assert [k.key_id for k in load_trust_store()] == ["good"]


def test_key_id_defaults_to_a_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, public = generate_keypair_hex()
    store = _write_store(tmp_path, {"public_key_hex": public})
    monkeypatch.setenv("BASELITH_PLUGIN_TRUST_STORE", str(store))
    assert load_trust_store()[0].key_id == public[:16]


# ── Expiry and revocation ────────────────────────────────────────────────────


def test_expired_key_is_not_a_trust_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, public = generate_keypair_hex()
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    store = _write_store(
        tmp_path, {"key_id": "old", "public_key_hex": public, "not_after": past}
    )
    monkeypatch.setenv("BASELITH_PLUGIN_TRUST_STORE", str(store))
    assert load_trust_roots() == []
    assert load_trust_store()[0].is_usable is False


def test_future_expiry_is_usable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, public = generate_keypair_hex()
    future = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    store = _write_store(
        tmp_path, {"key_id": "live", "public_key_hex": public, "not_after": future}
    )
    monkeypatch.setenv("BASELITH_PLUGIN_TRUST_STORE", str(store))
    assert load_trust_roots() == [public]


def test_naive_not_after_is_read_as_utc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, public = generate_keypair_hex()
    store = _write_store(
        tmp_path,
        {"key_id": "naive", "public_key_hex": public, "not_after": "2000-01-01"},
    )
    monkeypatch.setenv("BASELITH_PLUGIN_TRUST_STORE", str(store))
    assert load_trust_roots() == []


def test_unparseable_not_after_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, public = generate_keypair_hex()
    store = _write_store(
        tmp_path,
        {"key_id": "garbled", "public_key_hex": public, "not_after": "whenever"},
    )
    monkeypatch.setenv("BASELITH_PLUGIN_TRUST_STORE", str(store))
    assert load_trust_roots() == []


def test_trusted_key_rejection_reason() -> None:
    key = TrustedKey(key_id="x", public_key_hex="ab" * 32, revoked=True)
    assert "revoked" in (key.rejection_reason() or "")
    expired = TrustedKey(
        key_id="y",
        public_key_hex="ab" * 32,
        not_after=datetime(2000, 1, 1, tzinfo=UTC),
    )
    assert "expired" in (expired.rejection_reason() or "")
    assert TrustedKey(key_id="z", public_key_hex="ab" * 32).rejection_reason() is None


# ── Enforcement ──────────────────────────────────────────────────────────────


def test_enforcement_accepts_a_live_store_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private, public = generate_keypair_hex()
    store = _write_store(tmp_path, {"key_id": "publisher", "public_key_hex": public})
    monkeypatch.setenv("BASELITH_PLUGIN_TRUST_STORE", str(store))
    monkeypatch.setenv("BASELITH_REQUIRE_PLUGIN_SIGNATURES", "true")
    signature = sign_plugin_hash(HASH, private)
    assert enforce_plugin_signature("demo", HASH, signature) is True


def test_enforcement_refuses_a_revoked_key_and_names_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    private, public = generate_keypair_hex()
    store = _write_store(
        tmp_path,
        {"key_id": "leaked-2026", "public_key_hex": public, "revoked": True},
    )
    monkeypatch.setenv("BASELITH_PLUGIN_TRUST_STORE", str(store))
    monkeypatch.setenv("BASELITH_REQUIRE_PLUGIN_SIGNATURES", "true")
    signature = sign_plugin_hash(HASH, private)
    with caplog.at_level(logging.ERROR, logger="core.plugins.signing"):
        assert enforce_plugin_signature("demo", HASH, signature) is False
    assert "leaked-2026" in caplog.text
    assert "revoked" in caplog.text.lower()


def test_enforcement_refuses_an_expired_key_and_names_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    private, public = generate_keypair_hex()
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    store = _write_store(
        tmp_path,
        {"key_id": "lapsed", "public_key_hex": public, "not_after": past},
    )
    monkeypatch.setenv("BASELITH_PLUGIN_TRUST_STORE", str(store))
    monkeypatch.setenv("BASELITH_REQUIRE_PLUGIN_SIGNATURES", "true")
    signature = sign_plugin_hash(HASH, private)
    with caplog.at_level(logging.ERROR, logger="core.plugins.signing"):
        assert enforce_plugin_signature("demo", HASH, signature) is False
    assert "lapsed" in caplog.text
    assert "expired" in caplog.text.lower()


def test_enforcement_is_a_noop_when_not_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, public = generate_keypair_hex()
    store = _write_store(
        tmp_path, {"key_id": "k", "public_key_hex": public, "revoked": True}
    )
    monkeypatch.setenv("BASELITH_PLUGIN_TRUST_STORE", str(store))
    assert enforce_plugin_signature("demo", HASH, None) is True


def test_enforcement_refuses_when_nothing_is_configured(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("BASELITH_REQUIRE_PLUGIN_SIGNATURES", "true")
    with caplog.at_level(logging.ERROR, logger="core.plugins.signing"):
        assert enforce_plugin_signature("demo", HASH, "ab" * 32) is False
    assert "trust root" in caplog.text.lower()
