"""Loader contract: entry-point resolution, module teardown, discovery merge.

Three findings are pinned here:

* the plugin class used to be picked by ``dir(module)`` order — alphabetically —
  and the manifest's ``entry_point`` was never read;
* ``_unload_module`` dropped only two ``sys.modules`` keys, so a reloaded
  plugin kept serving its stale submodules;
* discovery only ever walked a directory, so a plugin installed as a wheel
  declaring the ``baselith.plugins`` entry-point group was invisible.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from core.plugins import PluginLoader, PluginRegistry
from core.plugins.discovery import ENTRY_POINT_GROUP, merge_plugin_dirs

TWO_CLASSES = """
from core.plugins import Plugin


class AlphaPlugin(Plugin):
    async def initialize(self, config=None):
        self._initialized = True


class ZuluPlugin(Plugin):
    async def initialize(self, config=None):
        self._initialized = True
"""

ONE_CLASS = """
from core.plugins import Plugin


class SoloPlugin(Plugin):
    async def initialize(self, config=None):
        self._initialized = True
"""


def _write_plugin(
    root: Path,
    name: str,
    *,
    body: str,
    manifest_extra: str = "",
) -> Path:
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "manifest.yaml").write_text(
        f"name: {name}\nversion: 1.0.0\n{manifest_extra}",
        encoding="utf-8",
    )
    (plugin_dir / "plugin.py").write_text(body, encoding="utf-8")
    return plugin_dir


@pytest.fixture
def registry() -> PluginRegistry:
    return PluginRegistry()


@pytest.fixture(autouse=True)
def _clean_sys_modules():
    before = set(sys.modules)
    yield
    for key in set(sys.modules) - before:
        if key.startswith("plugins."):
            del sys.modules[key]


class TestEntryPointResolution:
    @pytest.mark.asyncio
    async def test_entry_point_selects_declared_class(self, registry, tmp_path):
        _write_plugin(
            tmp_path,
            "two_classes",
            body=TWO_CLASSES,
            manifest_extra="entry_point: plugin:ZuluPlugin\n",
        )
        loader = PluginLoader(tmp_path, registry)
        plugin = await loader.load_plugin(tmp_path / "two_classes", initialize=False)
        assert plugin is not None
        assert type(plugin).__name__ == "ZuluPlugin"

    @pytest.mark.asyncio
    async def test_ambiguous_module_is_refused(self, registry, tmp_path):
        """Without an entry_point, two candidate classes is a load failure.

        The old heuristic silently took the alphabetically first one.
        """
        _write_plugin(tmp_path, "ambiguous", body=TWO_CLASSES)
        loader = PluginLoader(tmp_path, registry)
        plugin = await loader.load_plugin(tmp_path / "ambiguous", initialize=False)
        assert plugin is None

    @pytest.mark.asyncio
    async def test_single_class_still_resolved_without_entry_point(
        self, registry, tmp_path
    ):
        _write_plugin(tmp_path, "solo", body=ONE_CLASS)
        loader = PluginLoader(tmp_path, registry)
        plugin = await loader.load_plugin(tmp_path / "solo", initialize=False)
        assert plugin is not None
        assert type(plugin).__name__ == "SoloPlugin"

    @pytest.mark.asyncio
    async def test_imported_foreign_class_does_not_create_ambiguity(
        self, registry, tmp_path
    ):
        """A concrete Plugin imported from elsewhere loses to the plugin's own."""
        vendor_root = tmp_path / "vendor"
        vendor_root.mkdir()
        (vendor_root / "vendorlib.py").write_text(
            ONE_CLASS.replace("SoloPlugin", "VendorBasePlugin"), encoding="utf-8"
        )
        import sys as _sys

        _sys.path.insert(0, str(vendor_root))
        try:
            _write_plugin(
                tmp_path,
                "with_vendor",
                body="from vendorlib import VendorBasePlugin\n" + ONE_CLASS,
            )
            loader = PluginLoader(tmp_path, registry)
            plugin = await loader.load_plugin(
                tmp_path / "with_vendor", initialize=False
            )
        finally:
            _sys.path.remove(str(vendor_root))
            _sys.modules.pop("vendorlib", None)

        assert plugin is not None
        assert type(plugin).__name__ == "SoloPlugin"

    @pytest.mark.asyncio
    async def test_entry_point_may_name_a_submodule(self, registry, tmp_path):
        plugin_dir = _write_plugin(
            tmp_path,
            "submod",
            body="# nothing here\n",
            manifest_extra="entry_point: impl:DeepPlugin\n",
        )
        (plugin_dir / "impl.py").write_text(
            ONE_CLASS.replace("SoloPlugin", "DeepPlugin"), encoding="utf-8"
        )
        loader = PluginLoader(tmp_path, registry)
        plugin = await loader.load_plugin(plugin_dir, initialize=False)
        assert plugin is not None
        assert type(plugin).__name__ == "DeepPlugin"

    @pytest.mark.asyncio
    async def test_entry_point_naming_missing_class_fails(self, registry, tmp_path):
        _write_plugin(
            tmp_path,
            "wrong_name",
            body=ONE_CLASS,
            manifest_extra="entry_point: plugin:NotThere\n",
        )
        loader = PluginLoader(tmp_path, registry)
        plugin = await loader.load_plugin(tmp_path / "wrong_name", initialize=False)
        assert plugin is None

    @pytest.mark.asyncio
    async def test_entry_point_naming_non_plugin_fails(self, registry, tmp_path):
        _write_plugin(
            tmp_path,
            "not_a_plugin",
            body=ONE_CLASS + "\n\nclass Bare:\n    pass\n",
            manifest_extra="entry_point: plugin:Bare\n",
        )
        loader = PluginLoader(tmp_path, registry)
        plugin = await loader.load_plugin(tmp_path / "not_a_plugin", initialize=False)
        assert plugin is None

    @pytest.mark.asyncio
    async def test_bare_class_name_entry_point_resolves(self, registry, tmp_path):
        """``entry_point: SoloPlugin`` (no module part) uses the loaded module."""
        _write_plugin(
            tmp_path,
            "bare_name",
            body=ONE_CLASS,
            manifest_extra="entry_point: SoloPlugin\n",
        )
        loader = PluginLoader(tmp_path, registry)
        plugin = await loader.load_plugin(tmp_path / "bare_name", initialize=False)
        assert plugin is not None
        assert type(plugin).__name__ == "SoloPlugin"

    @pytest.mark.asyncio
    async def test_file_style_entry_point_falls_back_to_the_heuristic(
        self, registry, tmp_path
    ):
        """``entrypoint: __init__.py`` names the entry *file*, not a class.

        Marketplace manifests spell it that way. Reading it as ``module:Class``
        made every such plugin fail to load with "has no '__init__.py'".
        """
        _write_plugin(
            tmp_path,
            "file_style",
            body=ONE_CLASS,
            manifest_extra="entrypoint: __init__.py\n",
        )
        loader = PluginLoader(tmp_path, registry)
        plugin = await loader.load_plugin(tmp_path / "file_style", initialize=False)
        assert plugin is not None
        assert type(plugin).__name__ == "SoloPlugin"

    @pytest.mark.asyncio
    async def test_file_path_entry_point_falls_back_to_the_heuristic(
        self, registry, tmp_path
    ):
        _write_plugin(
            tmp_path,
            "file_path",
            body=ONE_CLASS,
            manifest_extra="entrypoint: src/plugin.py\n",
        )
        loader = PluginLoader(tmp_path, registry)
        plugin = await loader.load_plugin(tmp_path / "file_path", initialize=False)
        assert plugin is not None
        assert type(plugin).__name__ == "SoloPlugin"

    @pytest.mark.asyncio
    async def test_file_style_entry_point_does_not_resolve_ambiguity(
        self, registry, tmp_path
    ):
        """Tolerating a file name is not licence to guess between two classes."""
        _write_plugin(
            tmp_path,
            "file_style_ambiguous",
            body=TWO_CLASSES,
            manifest_extra="entrypoint: __init__.py\n",
        )
        loader = PluginLoader(tmp_path, registry)
        plugin = await loader.load_plugin(
            tmp_path / "file_style_ambiguous", initialize=False
        )
        assert plugin is None

    @pytest.mark.asyncio
    async def test_module_qualified_non_identifier_class_still_fails(
        self, registry, tmp_path
    ):
        """``module:not-a-class`` is malformed, not a file name — still refused."""
        _write_plugin(
            tmp_path,
            "malformed",
            body=ONE_CLASS,
            manifest_extra="entry_point: plugin:not-a-class\n",
        )
        loader = PluginLoader(tmp_path, registry)
        plugin = await loader.load_plugin(tmp_path / "malformed", initialize=False)
        assert plugin is None


class TestModuleTeardown:
    @pytest.mark.asyncio
    async def test_unload_purges_every_submodule(self, registry, tmp_path):
        plugin_dir = _write_plugin(
            tmp_path,
            "withsub",
            body="from plugins.withsub.helper import VALUE\n" + ONE_CLASS,
        )
        (plugin_dir / "__init__.py").write_text("", encoding="utf-8")
        (plugin_dir / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")

        loader = PluginLoader(tmp_path, registry)
        plugin = await loader.load_plugin(plugin_dir, initialize=False)
        assert plugin is not None
        assert "plugins.withsub.helper" in sys.modules

        loader._unload_module("withsub")

        leftovers = [
            key
            for key in sys.modules
            if key == "plugins.withsub" or key.startswith("plugins.withsub.")
        ]
        assert leftovers == []

    def test_unload_of_unknown_plugin_is_a_noop(self, registry, tmp_path):
        loader = PluginLoader(tmp_path, registry)
        loader._unload_module("never-loaded")  # must not raise

    @pytest.mark.asyncio
    async def test_reload_serves_the_edited_submodule(self, registry, tmp_path):
        """The behaviour the purge exists for: a reload must run the new code.

        Dropping only ``plugins.<pkg>`` and ``plugins.<pkg>.plugin`` left the
        helper module cached, so a reloaded plugin kept serving the *old*
        helper while reporting a successful reload.
        """
        plugin_dir = _write_plugin(
            tmp_path,
            "reloadable",
            body=(
                "from plugins.reloadable.helper import VALUE\n"
                "from core.plugins import Plugin\n"
                "\n"
                "\n"
                "class ReloadablePlugin(Plugin):\n"
                "    async def initialize(self, config=None):\n"
                "        self._initialized = True\n"
                "\n"
                "    def value(self):\n"
                "        return VALUE\n"
            ),
        )
        (plugin_dir / "__init__.py").write_text("", encoding="utf-8")
        (plugin_dir / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")

        loader = PluginLoader(tmp_path, registry)
        first = await loader.load_plugin(plugin_dir, initialize=False)
        assert first is not None
        assert first.value() == 1

        # A different byte length as well as a different value: pyc
        # invalidation keys on (mtime, size) and a same-second, same-size
        # rewrite would be served from __pycache__ regardless of sys.modules.
        (plugin_dir / "helper.py").write_text("VALUE = 222\n", encoding="utf-8")
        loader._unload_module("reloadable")
        loader.invalidate_discovery_cache()

        second = await loader.load_plugin(plugin_dir, initialize=False)
        assert second is not None
        assert second.value() == 222, "reload served the stale submodule"


class _FakeEntryPoint:
    def __init__(self, name: str, value: str) -> None:
        self.name = name
        self.value = value
        self.group = ENTRY_POINT_GROUP


class TestDiscoveryMerge:
    def test_entry_point_dirs_are_appended(self, tmp_path):
        local = tmp_path / "local" / "alpha"
        local.mkdir(parents=True)
        installed = tmp_path / "site" / "beta"
        installed.mkdir(parents=True)

        merged = merge_plugin_dirs([local], [installed])
        assert merged == [local, installed]

    def test_directory_wins_on_name_clash(self, tmp_path, monkeypatch):
        from unittest.mock import MagicMock

        from core.plugins import discovery as discovery_module

        fake_logger = MagicMock()
        monkeypatch.setattr(discovery_module, "logger", fake_logger)

        local = tmp_path / "local" / "alpha"
        local.mkdir(parents=True)
        installed = tmp_path / "site" / "alpha"
        installed.mkdir(parents=True)

        merged = merge_plugin_dirs([local], [installed])
        assert merged == [local]
        assert installed not in merged
        # The shadowing is announced, not silent.
        assert fake_logger.warning.call_count == 1
        assert "the directory wins" in fake_logger.warning.call_args.args[0]

    def test_same_path_twice_is_not_a_clash(self, tmp_path):
        local = tmp_path / "local" / "alpha"
        local.mkdir(parents=True)
        assert merge_plugin_dirs([local], [local]) == [local]

    def test_loader_discovers_entry_point_plugins(
        self, registry, tmp_path, monkeypatch
    ):
        scanned = tmp_path / "scanned"
        scanned.mkdir()
        _write_plugin(scanned, "from_dir", body=ONE_CLASS)

        external_root = tmp_path / "site-packages"
        external = _write_plugin(external_root, "from_wheel", body=ONE_CLASS)
        (external / "__init__.py").write_text("", encoding="utf-8")

        monkeypatch.syspath_prepend(str(external_root))
        monkeypatch.setattr(
            "core.plugins.discovery.entry_points",
            lambda group: [_FakeEntryPoint("from_wheel", "from_wheel")],
        )

        loader = PluginLoader(scanned, registry)
        names = sorted(p.name for p in loader.discover_plugins())
        assert names == ["from_dir", "from_wheel"]

    def test_entry_point_without_manifest_is_skipped(
        self, registry, tmp_path, monkeypatch
    ):
        scanned = tmp_path / "scanned"
        scanned.mkdir()

        external_root = tmp_path / "site-packages"
        bogus = external_root / "no_manifest"
        bogus.mkdir(parents=True)
        (bogus / "__init__.py").write_text("", encoding="utf-8")

        monkeypatch.syspath_prepend(str(external_root))
        monkeypatch.setattr(
            "core.plugins.discovery.entry_points",
            lambda group: [_FakeEntryPoint("no_manifest", "no_manifest")],
        )

        loader = PluginLoader(scanned, registry)
        assert loader.discover_plugins() == []

    def test_entry_point_failure_does_not_break_discovery(
        self, registry, tmp_path, monkeypatch
    ):
        scanned = tmp_path / "scanned"
        scanned.mkdir()
        _write_plugin(scanned, "from_dir", body=ONE_CLASS)

        def _boom(group):
            raise RuntimeError("metadata backend exploded")

        monkeypatch.setattr("core.plugins.discovery.entry_points", _boom)

        loader = PluginLoader(scanned, registry)
        assert [p.name for p in loader.discover_plugins()] == ["from_dir"]
