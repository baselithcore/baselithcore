"""An overlay entry never shadows a bundled plugin that is as new or newer."""

from __future__ import annotations

import logging
import sys
import types
from pathlib import Path

import pytest

from core.plugins import _overlay_guard, overlay
from core.plugins.integrity import compute_plugin_hash
from core.plugins.overlay_prune import prune_stale_overlay
from core.plugins.signing import generate_keypair_hex, sign_plugin_hash


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(overlay, "_REGISTERED", {})


@pytest.fixture()
def keys() -> tuple[str, str]:
    return generate_keypair_hex()


def _plugin(base: Path, dirname: str, name: str, version: str, extra: str = "") -> Path:
    d = base / dirname
    d.mkdir(parents=True)
    (d / "__init__.py").write_text("X = 1\n")
    (d / "manifest.yaml").write_text(
        f"name: {name}\nversion: {version}\nhash_surface_version: 5\n{extra}"
    )
    return d


def _signed_entry(
    root: Path, name: str, version: str, private_hex: str, extra: str = ""
) -> Path:
    real = _plugin(
        root / overlay.STORE_DIRNAME, f"{name}-{version}", name, version, extra
    )
    digest = compute_plugin_hash(real)
    manifest = real / "manifest.yaml"
    manifest.write_text(
        manifest.read_text()
        + f"integrity_sha256: {digest}\nsignature_ed25519: {sign_plugin_hash(digest, private_hex)}\n"
    )
    link = root / name
    link.symlink_to(real, target_is_directory=True)
    return link


def _register(
    root: Path, bundled: Path, public_hex: str, monkeypatch: pytest.MonkeyPatch
) -> list[str]:
    import plugins  # noqa: F401  # the real package first, as in production

    monkeypatch.setattr(overlay, "_trusted_public_keys", lambda _name: [public_hex])
    return overlay.register_overlay_packages(
        root, bundled_root=bundled, core_version="1.50.0"
    )


def test_older_entry_refused_and_logged(
    tmp_path: Path,
    keys: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    bundled = tmp_path / "bundled"
    _plugin(bundled, "guard_old", "guard_old", "1.5.0")
    root = tmp_path / "ov"
    _signed_entry(root, "guard_old", "1.3.0", keys[0])
    with caplog.at_level(logging.WARNING, logger="core.plugins.overlay"):
        assert _register(root, bundled, keys[1], monkeypatch) == []
    assert (
        "guard_old refused (not_newer: overlay 1.3.0 <= bundled 1.5.0)" in caplog.text
    )


def test_equal_version_refused(
    tmp_path: Path, keys: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    bundled = tmp_path / "bundled"
    _plugin(bundled, "guard_eq", "guard_eq", "1.3.0")
    root = tmp_path / "ov"
    _signed_entry(root, "guard_eq", "1.3.0", keys[0])
    assert _register(root, bundled, keys[1], monkeypatch) == []


def test_newer_entry_registered(
    tmp_path: Path, keys: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    bundled = tmp_path / "bundled"
    _plugin(bundled, "guard_new", "guard_new", "1.3.0")
    root = tmp_path / "ov"
    _signed_entry(root, "guard_new", "1.4.0", keys[0])
    monkeypatch.delitem(sys.modules, "plugins.guard_new", raising=False)
    assert _register(root, bundled, keys[1], monkeypatch) == ["guard_new"]


def test_entry_without_bundled_counterpart_registered(
    tmp_path: Path, keys: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "ov"
    _signed_entry(root, "guard_only", "0.1.0", keys[0])
    monkeypatch.delitem(sys.modules, "plugins.guard_only", raising=False)
    assert _register(root, tmp_path / "bundled", keys[1], monkeypatch) == ["guard_only"]


def test_incompatible_core_refused(
    tmp_path: Path,
    keys: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    bundled = tmp_path / "bundled"
    _plugin(bundled, "guard_core", "guard_core", "1.0.0")
    root = tmp_path / "ov"
    _signed_entry(root, "guard_core", "2.0.0", keys[0], "min_core_version: 99.0.0\n")
    with caplog.at_level(logging.WARNING, logger="core.plugins.overlay"):
        assert _register(root, bundled, keys[1], monkeypatch) == []
    assert "guard_core refused (incompatible_core" in caplog.text


def test_unreadable_bundled_version_refuses(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    _plugin(bundled, "guard_bad", "guard_bad", "not-a-version")
    entry = _plugin(tmp_path / "ov", "guard_bad", "guard_bad", "1.0.0")
    reason = _overlay_guard.overlay_refusal(entry, bundled, "1.50.0")
    assert reason is not None and reason.startswith("not_newer")


def test_invalid_entry_version_refuses(tmp_path: Path) -> None:
    entry = _plugin(tmp_path / "ov", "guard_v", "guard_v", "one")
    assert (
        _overlay_guard.overlay_refusal(entry, None, "1.50.0")
        == "version_invalid: 'one'"
    )


def test_default_bundled_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pkg = tmp_path / "plugins"
    monkeypatch.setitem(
        sys.modules, "plugins", types.SimpleNamespace(__file__=str(pkg / "__init__.py"))
    )
    assert _overlay_guard.default_bundled_root() == pkg.resolve()
    monkeypatch.setitem(sys.modules, "plugins", types.SimpleNamespace())
    assert _overlay_guard.default_bundled_root() is None


def test_prune_removes_only_stale_entries(
    tmp_path: Path, keys: tuple[str, str]
) -> None:
    private_hex = keys[0]
    bundled = tmp_path / "bundled"
    _plugin(bundled, "p_old", "p_old", "2.0.0")
    _plugin(bundled, "p_new", "p_new", "1.0.0")
    _plugin(bundled, "p_plain", "p_plain", "1.0.0")
    root = tmp_path / "ov"
    store = root / overlay.STORE_DIRNAME
    _signed_entry(root, "p_old", "1.0.0", private_hex)  # stale link + store dir
    _plugin(store, "p_old-1.5.0", "p_old", "1.5.0")  # stale, unlinked
    _signed_entry(root, "p_new", "1.1.0", private_hex)  # newer: kept
    _plugin(store, "p_new-0.9.0", "p_new", "0.9.0")  # older than bundled
    _plugin(root, "p_plain", "p_plain", "0.1.0")  # plain dir: reported, kept
    removed = prune_stale_overlay(root, bundled)
    assert sorted(p.name for p in removed) == [
        "p_new-0.9.0",
        "p_old",
        "p_old-1.0.0",
        "p_old-1.5.0",
    ]
    assert (root / "p_new").is_symlink() and (store / "p_new-1.1.0").is_dir()
    assert (root / "p_plain").is_dir()
    assert prune_stale_overlay(root, bundled) == []


def test_refusal_codes_tell_an_unreadable_bundled_version_from_an_older_entry(
    tmp_path: Path,
) -> None:
    bundled = tmp_path / "bundled"
    _plugin(bundled, "guard_bad", "guard_bad", "not-a-version")
    _plugin(bundled, "guard_new", "guard_new", "2.0.0")
    bad = _plugin(tmp_path / "ov", "guard_bad", "guard_bad", "1.0.0")
    old = _plugin(tmp_path / "ov", "guard_new", "guard_new", "1.0.0")
    code = _overlay_guard.OverlayRefusal
    assert (
        _overlay_guard.overlay_refusal_code(bad, bundled, "1.50.0")[0]
        is code.BUNDLED_UNREADABLE
    )  # type: ignore[index]
    assert (
        _overlay_guard.overlay_refusal_code(old, bundled, "1.50.0")[0] is code.NOT_NEWER
    )  # type: ignore[index]
    assert (
        _overlay_guard.overlay_refusal(old, bundled, "1.50.0")
        == "not_newer: overlay 1.0.0 <= bundled 2.0.0"
    )
    assert _overlay_guard.overlay_refusal_code(old, None, "1.50.0") is None
