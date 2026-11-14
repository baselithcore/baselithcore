"""Verify the marketplace installer runs integrity verification *before* the
``pip install`` build backend can execute arbitrary code.

Covers ``PluginInstaller._verify_integrity_pre_install`` fail-closed behaviour:
a declared-but-mismatched hash is rejected, strict mode rejects unsigned
plugins, a matching hash passes, and any internal error fails closed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from baselith.core.marketplace.installer import PluginInstaller
from baselith.core.plugins.integrity import compute_plugin_hash


@pytest.fixture(autouse=True)
def _deterministic_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin integrity flags regardless of ``.env``.

    ``get_plugin_config()`` loads ``.env`` (which sets
    ``BASELITH_SKIP_INTEGRITY_CHECK=true``) into ``os.environ`` when the
    installer is constructed, so env-var control is unreliable here. Patch the
    predicate functions directly: skip disabled, strict off by default.

    They must be patched on ``baselith.core.plugins.integrity_policy``, where the
    verifier calls them. ``baselith.core.plugins.integrity`` only re-exports the pair, so
    patching it there rebound a name nothing reads — and the ambient
    ``.env`` skip flag stayed in force, which made a tampered hash verify
    clean.
    """
    monkeypatch.setattr(
        "baselith.core.plugins.integrity_policy.is_skip_check_enabled", lambda: False
    )
    monkeypatch.setattr(
        "baselith.core.plugins.integrity_policy.is_strict_mode_enabled", lambda: False
    )


@pytest.fixture
def installer() -> PluginInstaller:
    return PluginInstaller()


def _make_plugin(root: Path, *, with_hash: bool | str = False) -> Path:
    root.mkdir()
    (root / "plugin.py").write_text("def hello():\n    return 'hi'\n", encoding="utf-8")
    manifest_data: dict[str, object] = {
        "name": "demo",
        "version": "1.0.0",
        "author": "tester",
    }
    if with_hash is True:
        # inject the real digest after writing sources
        (root / "manifest.yaml").write_text(
            yaml.safe_dump(manifest_data), encoding="utf-8"
        )
        manifest_data["integrity_sha256"] = compute_plugin_hash(root)
    elif isinstance(with_hash, str):
        manifest_data["integrity_sha256"] = with_hash
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest_data), encoding="utf-8")
    return root


def test_matching_hash_passes(installer: PluginInstaller, tmp_path: Path) -> None:
    plugin = _make_plugin(tmp_path / "demo", with_hash=True)
    assert installer._verify_integrity_pre_install(plugin) is True


def test_tampered_hash_rejected(installer: PluginInstaller, tmp_path: Path) -> None:
    plugin = _make_plugin(tmp_path / "demo", with_hash="00" * 32)
    assert installer._verify_integrity_pre_install(plugin) is False


def test_strict_mode_rejects_unsigned(
    installer: PluginInstaller, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The policy half of the integrity module moved to
    # ``baselith.core.plugins.integrity_policy``; ``baselith.core.plugins.integrity`` only
    # re-exports it, so patching the re-export no longer reaches the call site.
    monkeypatch.setattr(
        "baselith.core.plugins.integrity_policy.is_strict_mode_enabled", lambda: True
    )
    plugin = _make_plugin(tmp_path / "demo", with_hash=False)
    assert installer._verify_integrity_pre_install(plugin) is False


def test_errors_fail_closed(
    installer: PluginInstaller, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin = _make_plugin(tmp_path / "demo", with_hash=True)

    def _boom(*_args: object, **_kwargs: object) -> bool:
        raise RuntimeError("verifier exploded")

    monkeypatch.setattr(
        "baselith.core.plugins.integrity.verify_plugin_integrity", _boom
    )
    assert installer._verify_integrity_pre_install(plugin) is False
