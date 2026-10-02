"""Plugin overlay: verified entries shadow bundled plugins (spec §6.2)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from core.plugins import overlay
from core.plugins.discovery import MANIFEST_FILENAMES, apply_overlay
from core.plugins.integrity import compute_plugin_hash
from core.plugins.signing import generate_keypair_hex, sign_plugin_hash


def _make_plugin(base: Path, name: str, version: str, body: str = "X = 1\n") -> Path:
    d = base / name
    d.mkdir(parents=True)
    (d / "__init__.py").write_text(body)
    (d / "manifest.yaml").write_text(
        f"name: {name}\nversion: {version}\nhash_surface_version: 5\n"
    )
    return d


def _sign(d: Path, private_hex: str) -> None:
    h = compute_plugin_hash(d)
    sig = sign_plugin_hash(h, private_hex)
    manifest = d / "manifest.yaml"
    manifest.write_text(
        manifest.read_text() + f"integrity_sha256: {h}\nsignature_ed25519: {sig}\n"
    )


@pytest.fixture()
def keys() -> tuple[str, str]:
    return generate_keypair_hex()


@pytest.fixture(autouse=True)
def _reset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(overlay, "_REGISTERED", {})


def _store_entry(root: Path, name: str, version: str, private_hex: str) -> Path:
    real = _make_plugin(root / overlay.STORE_DIRNAME, f"{name}-{version}", version)
    manifest = real / "manifest.yaml"
    manifest.write_text(
        manifest.read_text().replace(f"name: {name}-{version}", f"name: {name}")
    )
    _sign(real, private_hex)
    link = root / name
    link.symlink_to(real, target_is_directory=True)
    return link


def test_manifest_filenames_match_discovery() -> None:
    assert overlay._MANIFEST_FILENAMES == MANIFEST_FILENAMES


def test_overlay_root_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(overlay.OVERLAY_ENV, raising=False)
    assert overlay.overlay_root() is None


def test_signed_store_entry_verifies(tmp_path: Path, keys: tuple[str, str]) -> None:
    private_hex, public_hex = keys
    link = _store_entry(tmp_path, "demo_ov", "1.1.0", private_hex)
    assert overlay.verify_overlay_entry(link, [public_hex]) is None


def test_unsigned_overlay_entry_rejected(tmp_path: Path, keys: tuple[str, str]) -> None:
    _, public_hex = keys
    d = _make_plugin(tmp_path, "demo_ov", "1.1.0")
    assert overlay.verify_overlay_entry(d, [public_hex]) == "signature_invalid"


def test_symlink_outside_store_rejected(tmp_path: Path, keys: tuple[str, str]) -> None:
    private_hex, public_hex = keys
    outside = _make_plugin(tmp_path / "elsewhere", "demo_ov", "1.1.0")
    _sign(outside, private_hex)
    root = tmp_path / "overlay"
    root.mkdir()
    (root / "demo_ov").symlink_to(outside, target_is_directory=True)
    assert (
        overlay.verify_overlay_entry(root / "demo_ov", [public_hex]) == "escapes_store"
    )


def test_name_mismatch_rejected(tmp_path: Path, keys: tuple[str, str]) -> None:
    private_hex, public_hex = keys
    real = _make_plugin(tmp_path / overlay.STORE_DIRNAME, "other-1.0.0", "1.0.0")
    _sign(real, private_hex)
    (tmp_path / "demo_ov").symlink_to(real, target_is_directory=True)
    assert (
        overlay.verify_overlay_entry(tmp_path / "demo_ov", [public_hex])
        == "name_mismatch"
    )


def test_register_and_import_from_overlay(
    tmp_path: Path, keys: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import plugins  # noqa: F401  # the real package first, as in production; else a bare stub wins

    private_hex, public_hex = keys
    link = _store_entry(tmp_path, "demo_ov", "1.1.0", private_hex)
    monkeypatch.setattr(overlay, "_trusted_public_keys", lambda _name: [public_hex])
    monkeypatch.delitem(sys.modules, "plugins.demo_ov", raising=False)
    assert overlay.register_overlay_packages(tmp_path) == ["demo_ov"]
    import plugins.demo_ov as mod  # type: ignore[import-not-found]

    # The registered package is a lazy stub: __file__ appears only after the
    # first non-dunder attribute access, so assert on __path__ and on a value.
    assert Path(mod.__path__[0]).resolve().is_relative_to(tmp_path.resolve())
    assert mod.X == 1
    assert overlay.registered_overlay_dirs() == [link]
    # idempotent
    assert overlay.register_overlay_packages(tmp_path) == ["demo_ov"]


def test_no_trusted_keys_registers_nothing(
    tmp_path: Path, keys: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    private_hex, _ = keys
    _store_entry(tmp_path, "demo_ov2", "1.1.0", private_hex)
    monkeypatch.setattr(overlay, "_trusted_public_keys", lambda _name: [])
    assert overlay.register_overlay_packages(tmp_path) == []


def test_bundled_shadow_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundled = tmp_path / "plugins"
    fake = type(sys)("plugins.demo_shadow.sub")
    fake.__file__ = str(bundled / "demo_shadow" / "sub.py")
    monkeypatch.setitem(sys.modules, "plugins.demo_shadow.sub", fake)
    monkeypatch.setattr(
        overlay, "_REGISTERED", {"demo_shadow": tmp_path / "ov" / "demo_shadow"}
    )
    assert overlay.bundled_shadow_modules(bundled) == ["plugins.demo_shadow.sub"]


def test_apply_overlay_replaces_by_name(tmp_path: Path) -> None:
    a, b = tmp_path / "plugins" / "a", tmp_path / "plugins" / "b"
    ov_a = tmp_path / "ov" / "a"
    assert apply_overlay([a, b], [ov_a]) == [ov_a, b]


def test_registration_runs_on_first_plugins_import() -> None:
    # Read the file, not ``plugins.__file__``: other suites leave a bare stub in sys.modules.
    source = (
        Path(__file__).resolve().parents[4] / "plugins" / "__init__.py"
    ).read_text()
    assert "register_overlay_packages()" in source


def test_app_middleware_hook_comes_from_overlay(
    tmp_path: Path, keys: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sync pre-discovery must exec the overlay plugin.py, never the bundled one."""
    import plugins  # noqa: F401  # the real package first, as in production
    from core.plugins.app_setup import apply_plugin_app_middleware

    hook = (
        "from core.plugins import Plugin\n\n\n"
        "class OvMwPlugin(Plugin):\n"
        "    @classmethod\n"
        "    def setup_app_middleware(cls, app):\n"
        "        app.origin = {origin!r}\n"
    )
    private_hex, public_hex = keys
    bundled = tmp_path / "bundled"
    _make_plugin(bundled, "demo_mw", "1.0.0")
    (bundled / "demo_mw" / "plugin.py").write_text(hook.format(origin="bundled"))

    root = tmp_path / "overlay"
    real = _make_plugin(root / overlay.STORE_DIRNAME, "demo_mw-1.1.0", "1.1.0")
    manifest = real / "manifest.yaml"
    manifest.write_text(
        manifest.read_text().replace("name: demo_mw-1.1.0", "name: demo_mw")
    )
    (real / "plugin.py").write_text(hook.format(origin="overlay"))
    _sign(real, private_hex)
    (root / "demo_mw").symlink_to(real, target_is_directory=True)

    for mod in ("plugins.demo_mw", "plugins.demo_mw.plugin"):
        monkeypatch.delitem(sys.modules, mod, raising=False)
    monkeypatch.setattr(overlay, "_trusted_public_keys", lambda _name: [public_hex])
    assert overlay.register_overlay_packages(root) == ["demo_mw"]

    class _App:
        origin = ""

    app = _App()
    try:
        assert (
            apply_plugin_app_middleware(app, plugins_dir=bundled, plugin_configs={})
            == 1
        )
        assert app.origin == "overlay"
        assert overlay.bundled_shadow_modules(bundled) == []
    finally:
        for mod in ("plugins.demo_mw", "plugins.demo_mw.plugin"):
            sys.modules.pop(mod, None)


def _bundled_and_overlay(
    tmp_path: Path, private_hex: str, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    bundled_root = tmp_path / "bundled"
    _make_plugin(bundled_root, "demo", "1.0.0")
    root = tmp_path / "ov"
    link = _store_entry(root, "demo", "9.9.9", private_hex)
    monkeypatch.setattr(overlay, "_REGISTERED", {"demo": link})
    return bundled_root, link


def test_resolve_plugin_dir_prefers_overlay(
    tmp_path: Path, keys: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.plugins.loader import PluginLoader
    from core.plugins.registry import PluginRegistry

    bundled_root, link = _bundled_and_overlay(tmp_path, keys[0], monkeypatch)
    loader = PluginLoader(bundled_root, PluginRegistry())
    assert loader.resolve_plugin_dir("demo") == link


def test_resource_analyzer_reads_overlay_manifest(
    tmp_path: Path, keys: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.plugins.resource_analyzer import ResourceAnalyzer

    bundled_root, _ = _bundled_and_overlay(tmp_path, keys[0], monkeypatch)
    found = ResourceAnalyzer(bundled_root).discover_plugins({})
    assert found["demo"].metadata.version == "9.9.9"


def test_symlinked_subdirectory_refused(tmp_path: Path, keys: tuple[str, str]) -> None:
    """A link below the entry escapes the hash walk; the entry link itself is fine."""
    private_hex, public_hex = keys
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "evil.py").write_text("X = 2\n")
    real = _make_plugin(tmp_path / overlay.STORE_DIRNAME, "demo_ov-1.1.0", "1.1.0")
    manifest = real / "manifest.yaml"
    manifest.write_text(
        manifest.read_text().replace("name: demo_ov-1.1.0", "name: demo_ov")
    )
    (real / "sub").symlink_to(elsewhere, target_is_directory=True)
    _sign(real, private_hex)
    (tmp_path / "demo_ov").symlink_to(real, target_is_directory=True)
    assert overlay.verify_overlay_entry(tmp_path / "demo_ov", [public_hex]) == "symlink"


def test_malformed_entry_does_not_block_a_later_good_one(
    tmp_path: Path, keys: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import plugins  # noqa: F401  # the real package first, as in production

    private_hex, public_hex = keys
    bad = _make_plugin(tmp_path, "aaa_bad", "1.0.0")
    (bad / "manifest.yaml").write_text("name: [unclosed\n")
    _store_entry(tmp_path, "zzz_good", "1.1.0", private_hex)
    monkeypatch.setattr(overlay, "_trusted_public_keys", lambda _name: [public_hex])
    monkeypatch.delitem(sys.modules, "plugins.zzz_good", raising=False)
    assert overlay.register_overlay_packages(tmp_path) == ["zzz_good"]


def test_package_path_is_the_verified_store_dir_not_the_link(
    tmp_path: Path, keys: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Imports must resolve inside the tree that was hashed and signed.

    ``<overlay>/<name>`` is a symlink the updater swaps atomically while the
    API keeps running. Registering the link as ``__path__`` would make every
    lazy submodule import after a swap load from a tree this process never
    verified; registering the resolved store directory pins the imports to
    the verified release until the restart.
    """
    import plugins  # noqa: F401

    private_hex, public_hex = keys
    link = _store_entry(tmp_path, "demo_pin", "1.1.0", private_hex)
    monkeypatch.setattr(overlay, "_trusted_public_keys", lambda _name: [public_hex])
    monkeypatch.delitem(sys.modules, "plugins.demo_pin", raising=False)
    assert overlay.register_overlay_packages(tmp_path) == ["demo_pin"]
    pkg = sys.modules["plugins.demo_pin"]
    assert pkg.__path__ == [str(link.resolve())]
    assert pkg.__path__ != [str(link)]
