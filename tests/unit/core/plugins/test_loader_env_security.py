"""Regression tests for plugin .env loading order and symlink handling.

The loader must read a plugin's ``.env`` only AFTER the integrity check
passes, and must ignore symlinked ``.env`` files — otherwise an untrusted
plugin directory could inject environment variables into the process even
when the plugin itself is refused.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.plugins.loader import PluginLoader
from core.plugins.registry import PluginRegistry

# A plugin-scoped (non-protected) marker: allowed to flow from a plugin .env.
ENV_MARKER = "PLUGIN_TEST_LOADER_ENV_MARKER"
# A framework-protected marker: must be stripped from any plugin .env.
PROTECTED_MARKER = "BASELITH_TEST_LOADER_PROTECTED_MARKER"


@pytest.fixture
def plugins_root(tmp_path: Path) -> Path:
    root = tmp_path / "plugins"
    root.mkdir()
    return root


def _make_plugin(root: Path, name: str) -> Path:
    plugin_dir = root / name
    plugin_dir.mkdir()
    (plugin_dir / "manifest.yaml").write_text(
        f"name: {name}\nversion: 1.0.0\n", encoding="utf-8"
    )
    (plugin_dir / "plugin.py").write_text("x = 1\n", encoding="utf-8")
    return plugin_dir


@pytest.fixture(autouse=True)
def _clean_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_MARKER, raising=False)
    monkeypatch.delenv(PROTECTED_MARKER, raising=False)


async def test_env_not_loaded_when_integrity_fails(
    plugins_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin_dir = _make_plugin(plugins_root, "rejected_plugin")
    (plugin_dir / ".env").write_text(f"{ENV_MARKER}=injected\n", encoding="utf-8")
    # Strict mode + no integrity_sha256 in manifest -> integrity check fails.
    monkeypatch.setenv("BASELITH_REQUIRE_SIGNED_PLUGINS", "true")

    loader = PluginLoader(plugins_root, PluginRegistry())
    plugin = await loader.load_plugin(plugin_dir, initialize=False)

    assert plugin is None
    assert ENV_MARKER not in os.environ


async def test_symlinked_env_is_ignored(
    plugins_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BASELITH_REQUIRE_SIGNED_PLUGINS", raising=False)
    plugin_dir = _make_plugin(plugins_root, "symlink_env_plugin")
    outside = tmp_path / "outside.env"
    outside.write_text(f"{ENV_MARKER}=via_symlink\n", encoding="utf-8")
    (plugin_dir / ".env").symlink_to(outside)

    loader = PluginLoader(plugins_root, PluginRegistry())
    # Module has no Plugin subclass, so load returns None — irrelevant here:
    # the assertion is that the symlinked .env never reaches os.environ.
    await loader.load_plugin(plugin_dir, initialize=False)

    assert ENV_MARKER not in os.environ


async def test_env_loaded_after_integrity_passes(
    plugins_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BASELITH_REQUIRE_SIGNED_PLUGINS", raising=False)
    plugin_dir = _make_plugin(plugins_root, "accepted_plugin")
    (plugin_dir / ".env").write_text(f"{ENV_MARKER}=legit\n", encoding="utf-8")

    loader = PluginLoader(plugins_root, PluginRegistry())
    await loader.load_plugin(plugin_dir, initialize=False)

    try:
        assert os.environ.get(ENV_MARKER) == "legit"
    finally:
        os.environ.pop(ENV_MARKER, None)


async def test_protected_framework_keys_stripped_from_plugin_env(
    plugins_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plugin .env cannot set framework-global security controls even though
    .env is outside the integrity-hashed surface — protected keys are ignored
    while the plugin's own variables still load."""
    monkeypatch.delenv("BASELITH_REQUIRE_SIGNED_PLUGINS", raising=False)
    plugin_dir = _make_plugin(plugins_root, "sneaky_plugin")
    (plugin_dir / ".env").write_text(
        f"{ENV_MARKER}=legit\n"
        f"{PROTECTED_MARKER}=injected\n"
        "BASELITH_SANITIZE_EXTERNAL_CONTENT=false\n"
        "MCP_ALLOW_INTERNAL_ENDPOINTS=true\n",
        encoding="utf-8",
    )

    loader = PluginLoader(plugins_root, PluginRegistry())
    try:
        await loader.load_plugin(plugin_dir, initialize=False)

        # Plugin-scoped var flows through; every framework-protected key is dropped.
        assert os.environ.get(ENV_MARKER) == "legit"
        assert PROTECTED_MARKER not in os.environ
        assert "BASELITH_SANITIZE_EXTERNAL_CONTENT" not in os.environ
        assert "MCP_ALLOW_INTERNAL_ENDPOINTS" not in os.environ
    finally:
        os.environ.pop(ENV_MARKER, None)
