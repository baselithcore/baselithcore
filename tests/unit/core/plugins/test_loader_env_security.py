"""Regression tests for plugin .env loading order, symlinks and key scoping.

The loader must read a plugin's ``.env`` only AFTER the integrity check
passes, and must ignore symlinked ``.env`` files — otherwise an untrusted
plugin directory could inject environment variables into the process even
when the plugin itself is refused.

Since the loader converged on the namespace **allowlist** (``core.plugins._env``),
it must additionally refuse to export any key outside the plugin's own
``<DIRNAME>_`` namespace, whether or not that key appears on the protected-key
denylist. The denylist can only cover controls someone remembered to list; the
allowlist is what stops the rest.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.plugins._env import LEGACY_DENYLIST_ONLY_FLAG
from core.plugins.loader import PluginLoader
from core.plugins.registry import PluginRegistry

# An in-namespace marker for a plugin directory named ``<slug>``: allowed to
# flow from that plugin's .env into the process environment.
NS_MARKER = "NSPLUGIN_ENV_MARKER"
NS_PLUGIN = "nsplugin"
# An out-of-namespace but NOT denylisted marker: must no longer reach os.environ.
FOREIGN_MARKER = "PLUGIN_TEST_LOADER_ENV_MARKER"
# A framework-protected marker: must be stripped from any plugin .env.
PROTECTED_MARKER = "BASELITH_TEST_LOADER_PROTECTED_MARKER"

_ALL_MARKERS = (NS_MARKER, FOREIGN_MARKER, PROTECTED_MARKER)


@pytest.fixture
def plugins_root(tmp_path: Path) -> Path:
    root = tmp_path / "plugins"
    root.mkdir()
    return root


def _make_plugin(root: Path, name: str, *, manifest_extra: str = "") -> Path:
    plugin_dir = root / name
    plugin_dir.mkdir()
    (plugin_dir / "manifest.yaml").write_text(
        f"name: {name}\nversion: 1.0.0\n{manifest_extra}", encoding="utf-8"
    )
    (plugin_dir / "plugin.py").write_text("x = 1\n", encoding="utf-8")
    return plugin_dir


@pytest.fixture(autouse=True)
def _clean_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    for marker in _ALL_MARKERS:
        monkeypatch.delenv(marker, raising=False)
    monkeypatch.delenv(LEGACY_DENYLIST_ONLY_FLAG, raising=False)


async def test_env_not_loaded_when_integrity_fails(
    plugins_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plugin_dir = _make_plugin(plugins_root, NS_PLUGIN)
    (plugin_dir / ".env").write_text(f"{NS_MARKER}=injected\n", encoding="utf-8")
    # Strict mode + no integrity_sha256 in manifest -> integrity check fails.
    monkeypatch.setenv("BASELITH_REQUIRE_SIGNED_PLUGINS", "true")

    loader = PluginLoader(plugins_root, PluginRegistry())
    plugin = await loader.load_plugin(plugin_dir, initialize=False)

    assert plugin is None
    assert NS_MARKER not in os.environ


async def test_symlinked_env_is_ignored(
    plugins_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BASELITH_REQUIRE_SIGNED_PLUGINS", raising=False)
    plugin_dir = _make_plugin(plugins_root, NS_PLUGIN)
    outside = tmp_path / "outside.env"
    outside.write_text(f"{NS_MARKER}=via_symlink\n", encoding="utf-8")
    (plugin_dir / ".env").symlink_to(outside)

    loader = PluginLoader(plugins_root, PluginRegistry())
    # Module has no Plugin subclass, so load returns None — irrelevant here:
    # the assertion is that the symlinked .env never reaches os.environ.
    await loader.load_plugin(plugin_dir, initialize=False)

    assert NS_MARKER not in os.environ


async def test_env_loaded_after_integrity_passes(
    plugins_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The legitimate case still works: an in-namespace key is exported."""
    monkeypatch.delenv("BASELITH_REQUIRE_SIGNED_PLUGINS", raising=False)
    plugin_dir = _make_plugin(plugins_root, NS_PLUGIN)
    (plugin_dir / ".env").write_text(f"{NS_MARKER}=legit\n", encoding="utf-8")

    loader = PluginLoader(plugins_root, PluginRegistry())
    await loader.load_plugin(plugin_dir, initialize=False)

    try:
        assert os.environ.get(NS_MARKER) == "legit"
    finally:
        os.environ.pop(NS_MARKER, None)


async def test_protected_framework_keys_stripped_from_plugin_env(
    plugins_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plugin .env cannot set framework-global security controls even though
    .env is outside the integrity-hashed surface — protected keys are ignored
    while the plugin's own namespaced variables still load."""
    monkeypatch.delenv("BASELITH_REQUIRE_SIGNED_PLUGINS", raising=False)
    plugin_dir = _make_plugin(plugins_root, NS_PLUGIN)
    (plugin_dir / ".env").write_text(
        f"{NS_MARKER}=legit\n"
        f"{PROTECTED_MARKER}=injected\n"
        "BASELITH_SANITIZE_EXTERNAL_CONTENT=false\n"
        "MCP_ALLOW_INTERNAL_ENDPOINTS=true\n",
        encoding="utf-8",
    )

    loader = PluginLoader(plugins_root, PluginRegistry())
    try:
        await loader.load_plugin(plugin_dir, initialize=False)

        # Plugin-scoped var flows through; every framework-protected key is dropped.
        assert os.environ.get(NS_MARKER) == "legit"
        assert PROTECTED_MARKER not in os.environ
        assert "BASELITH_SANITIZE_EXTERNAL_CONTENT" not in os.environ
        assert "MCP_ALLOW_INTERNAL_ENDPOINTS" not in os.environ
    finally:
        os.environ.pop(NS_MARKER, None)


async def test_out_of_namespace_key_never_reaches_os_environ(
    plugins_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The core regression: a key outside the plugin namespace and on no
    denylist must not be exported by the loader path any more."""
    monkeypatch.delenv("BASELITH_REQUIRE_SIGNED_PLUGINS", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    monkeypatch.delenv("GIT_SSH_COMMAND", raising=False)
    plugin_dir = _make_plugin(plugins_root, NS_PLUGIN)
    (plugin_dir / ".env").write_text(
        f"{FOREIGN_MARKER}=injected\n"
        "AWS_SECRET_ACCESS_KEY=leak\n"
        "GIT_SSH_COMMAND=ssh -o ProxyCommand=evil\n",
        encoding="utf-8",
    )

    loader = PluginLoader(plugins_root, PluginRegistry())
    await loader.load_plugin(plugin_dir, initialize=False)

    assert FOREIGN_MARKER not in os.environ
    assert "AWS_SECRET_ACCESS_KEY" not in os.environ
    assert "GIT_SSH_COMMAND" not in os.environ


async def test_out_of_namespace_key_still_reaches_plugin_config(
    plugins_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compatibility: the key is still merged into the plugin's OWN config dict
    (a per-plugin surface), only the process-global export is withdrawn."""
    monkeypatch.delenv("BASELITH_REQUIRE_SIGNED_PLUGINS", raising=False)
    plugin_dir = _make_plugin(plugins_root, NS_PLUGIN)
    (plugin_dir / ".env").write_text(
        f"{FOREIGN_MARKER}=from_dotenv\n{PROTECTED_MARKER}=nope\n", encoding="utf-8"
    )

    # Seeded non-empty: the loader replaces a falsy config with a fresh dict.
    config: dict[str, object] = {"seed": True}
    loader = PluginLoader(plugins_root, PluginRegistry())
    await loader.load_plugin(plugin_dir, config=config, initialize=False)

    assert config[FOREIGN_MARKER.lower()] == "from_dotenv"
    # Protected keys are dropped from config too, not merely from os.environ.
    assert PROTECTED_MARKER.lower() not in config
    assert FOREIGN_MARKER not in os.environ


async def test_manifest_declared_key_is_exported(
    plugins_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Migration path: a legitimately un-namespaced key is allowed once the
    publisher declares it in the manifest's ``environment_variables``."""
    monkeypatch.delenv("BASELITH_REQUIRE_SIGNED_PLUGINS", raising=False)
    plugin_dir = _make_plugin(
        plugins_root,
        NS_PLUGIN,
        manifest_extra=f"environment_variables:\n  - {FOREIGN_MARKER}\n",
    )
    (plugin_dir / ".env").write_text(
        f"{FOREIGN_MARKER}=declared\n{PROTECTED_MARKER}=nope\n", encoding="utf-8"
    )

    loader = PluginLoader(plugins_root, PluginRegistry())
    try:
        await loader.load_plugin(plugin_dir, initialize=False)
        assert os.environ.get(FOREIGN_MARKER) == "declared"
        # A declaration widens the allowlist; it never disables the denylist.
        assert PROTECTED_MARKER not in os.environ
    finally:
        os.environ.pop(FOREIGN_MARKER, None)


async def test_manifest_cannot_declare_a_protected_key(
    plugins_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BASELITH_REQUIRE_SIGNED_PLUGINS", raising=False)
    plugin_dir = _make_plugin(
        plugins_root,
        NS_PLUGIN,
        manifest_extra=(
            "environment_variables:\n  - HTTPS_PROXY\n  - PYTHONPATH\n"
            f"  - {PROTECTED_MARKER}\n"
        ),
    )
    before_proxy = os.environ.get("HTTPS_PROXY")
    before_pythonpath = os.environ.get("PYTHONPATH")
    (plugin_dir / ".env").write_text(
        "HTTPS_PROXY=http://evil.example\n"
        "PYTHONPATH=/tmp/evil\n"
        f"{PROTECTED_MARKER}=1\n",
        encoding="utf-8",
    )

    loader = PluginLoader(plugins_root, PluginRegistry())
    await loader.load_plugin(plugin_dir, initialize=False)

    assert os.environ.get("HTTPS_PROXY") == before_proxy
    assert os.environ.get("PYTHONPATH") == before_pythonpath
    assert PROTECTED_MARKER not in os.environ


async def test_legacy_optout_restores_export_but_not_the_denylist(
    plugins_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The documented, deprecated escape hatch for un-migrated deployments."""
    monkeypatch.delenv("BASELITH_REQUIRE_SIGNED_PLUGINS", raising=False)
    monkeypatch.setenv(LEGACY_DENYLIST_ONLY_FLAG, "true")
    plugin_dir = _make_plugin(plugins_root, NS_PLUGIN)
    (plugin_dir / ".env").write_text(
        f"{FOREIGN_MARKER}=legacy\n{PROTECTED_MARKER}=nope\n"
        "MCP_ALLOW_INTERNAL_ENDPOINTS=true\n",
        encoding="utf-8",
    )

    loader = PluginLoader(plugins_root, PluginRegistry())
    try:
        await loader.load_plugin(plugin_dir, initialize=False)
        assert os.environ.get(FOREIGN_MARKER) == "legacy"
        # The opt-out widens the allowlist only — protected keys stay refused.
        assert PROTECTED_MARKER not in os.environ
        assert "MCP_ALLOW_INTERNAL_ENDPOINTS" not in os.environ
    finally:
        os.environ.pop(FOREIGN_MARKER, None)


async def test_existing_process_value_is_never_clobbered(
    plugins_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BASELITH_REQUIRE_SIGNED_PLUGINS", raising=False)
    monkeypatch.setenv(NS_MARKER, "from_process")
    plugin_dir = _make_plugin(plugins_root, NS_PLUGIN)
    (plugin_dir / ".env").write_text(f"{NS_MARKER}=from_dotenv\n", encoding="utf-8")

    loader = PluginLoader(plugins_root, PluginRegistry())
    await loader.load_plugin(plugin_dir, initialize=False)

    assert os.environ[NS_MARKER] == "from_process"
