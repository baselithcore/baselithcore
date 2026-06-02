"""Tests for plugin integrity verification."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.plugins.integrity import (
    compute_plugin_hash,
    enforce_signing_policy,
    is_strict_mode_enabled,
    verify_plugin_integrity,
)


def _clear_signing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "APP_ENV",
        "ENVIRONMENT",
        "BASELITH_REQUIRE_SIGNED_PLUGINS",
        "BASELITH_FAIL_ON_UNSIGNED_IN_PROD",
    ):
        monkeypatch.delenv(var, raising=False)


def test_enforce_signing_policy_noop_outside_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_signing_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "development")
    # No env, dev: must not raise.
    enforce_signing_policy()


def test_enforce_signing_policy_warns_in_prod_without_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_signing_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    # Default: warn-only, must NOT raise (no regression for existing deploys).
    enforce_signing_policy()


def test_enforce_signing_policy_noop_in_prod_when_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_signing_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("BASELITH_REQUIRE_SIGNED_PLUGINS", "true")
    enforce_signing_policy()


def test_enforce_signing_policy_hard_fail_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_signing_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("BASELITH_FAIL_ON_UNSIGNED_IN_PROD", "true")
    with pytest.raises(RuntimeError, match="Plugin signing is NOT enforced"):
        enforce_signing_policy()


def test_enforce_signing_policy_strict_overrides_hard_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_signing_env(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("BASELITH_REQUIRE_SIGNED_PLUGINS", "true")
    monkeypatch.setenv("BASELITH_FAIL_ON_UNSIGNED_IN_PROD", "true")
    # Strict satisfied -> no raise even with fail-on flag set.
    enforce_signing_policy()


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    """Create a minimal plugin directory tree for hashing."""
    root = tmp_path / "demo_plugin"
    root.mkdir()
    (root / "manifest.yaml").write_text(
        "name: demo\nversion: 1.0.0\n", encoding="utf-8"
    )
    (root / "plugin.py").write_text("def hello(): return 'hi'\n", encoding="utf-8")
    sub = root / "skills"
    sub.mkdir()
    (sub / "__init__.py").write_text("", encoding="utf-8")
    (sub / "module.pyi").write_text("def stub(): ...\n", encoding="utf-8")
    return root


def test_compute_hash_is_deterministic(plugin_dir: Path) -> None:
    h1 = compute_plugin_hash(plugin_dir)
    h2 = compute_plugin_hash(plugin_dir)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex


def test_compute_hash_changes_when_source_changes(plugin_dir: Path) -> None:
    before = compute_plugin_hash(plugin_dir)
    (plugin_dir / "plugin.py").write_text(
        "def hello(): return 'mutated'\n", encoding="utf-8"
    )
    after = compute_plugin_hash(plugin_dir)
    assert before != after


def test_compute_hash_excludes_pycache(plugin_dir: Path) -> None:
    baseline = compute_plugin_hash(plugin_dir)
    cache = plugin_dir / "__pycache__"
    cache.mkdir()
    (cache / "plugin.cpython-312.pyc").write_bytes(b"\x00\x01\x02")
    assert compute_plugin_hash(plugin_dir) == baseline


def test_compute_hash_excludes_node_modules_and_ui(plugin_dir: Path) -> None:
    baseline = compute_plugin_hash(plugin_dir)
    (plugin_dir / "node_modules").mkdir()
    (plugin_dir / "node_modules" / "thing.py").write_text("x", encoding="utf-8")
    (plugin_dir / "ui").mkdir()
    (plugin_dir / "ui" / "ignored.py").write_text("y", encoding="utf-8")
    assert compute_plugin_hash(plugin_dir) == baseline


def test_compute_hash_excludes_manifest(plugin_dir: Path) -> None:
    """Manifest is excluded so publishers can inject integrity_sha256 post-hash."""
    baseline = compute_plugin_hash(plugin_dir)
    (plugin_dir / "manifest.yaml").write_text(
        "name: demo\nversion: 1.0.0\nintegrity_sha256: deadbeef\n",
        encoding="utf-8",
    )
    assert compute_plugin_hash(plugin_dir) == baseline


def test_verify_passes_when_hash_matches(plugin_dir: Path) -> None:
    expected = compute_plugin_hash(plugin_dir)
    assert verify_plugin_integrity(plugin_dir, expected) is True


def test_verify_rejects_when_hash_differs(plugin_dir: Path) -> None:
    assert verify_plugin_integrity(plugin_dir, "deadbeef" * 8) is False


def test_verify_is_case_insensitive(plugin_dir: Path) -> None:
    expected = compute_plugin_hash(plugin_dir).upper()
    assert verify_plugin_integrity(plugin_dir, expected) is True


def test_verify_allows_unsigned_in_lax_mode(plugin_dir: Path) -> None:
    assert verify_plugin_integrity(plugin_dir, None, strict=False) is True


def test_verify_rejects_unsigned_in_strict_mode(plugin_dir: Path) -> None:
    assert verify_plugin_integrity(plugin_dir, None, strict=True) is False


def test_strict_mode_env_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BASELITH_REQUIRE_SIGNED_PLUGINS", raising=False)
    assert is_strict_mode_enabled() is False
    for value in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("BASELITH_REQUIRE_SIGNED_PLUGINS", value)
        assert is_strict_mode_enabled() is True
    for value in ("0", "false", "off", ""):
        monkeypatch.setenv("BASELITH_REQUIRE_SIGNED_PLUGINS", value)
        assert is_strict_mode_enabled() is False


def test_verify_uses_env_flag_when_strict_unset(
    plugin_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BASELITH_REQUIRE_SIGNED_PLUGINS", "true")
    assert verify_plugin_integrity(plugin_dir, None) is False
    monkeypatch.setenv("BASELITH_REQUIRE_SIGNED_PLUGINS", "false")
    assert verify_plugin_integrity(plugin_dir, None) is True
