"""Loader behaviour when a plugin's manifest is broken.

Two separate contracts live here:

* one bad plugin must never abort the load of the rest of the tree
  (``load_all_plugins`` is the boot path — a typo in a third-party manifest
  cannot be allowed to take the whole process's plugin set with it);
* a manifest that is *present but invalid* must be refused outright, in every
  environment. Loading it anyway runs the plugin with no discovery at all,
  which silently means no declared ``permissions`` (so the capability guards
  never deny anything), no ``min_core_version``, no declared
  ``environment_variables`` and no ``entry_point`` — strictly more privilege
  than the author asked for, granted because of a typo.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from core.plugins import PluginLoader, PluginRegistry

REPO_ROOT = Path(__file__).resolve().parents[4]
EXAMPLE_PLUGIN = REPO_ROOT / "plugins" / "example-plugin"

GOOD_PLUGIN = """
from core.plugins import Plugin


class GoodPlugin(Plugin):
    async def initialize(self, config=None):
        self._initialized = True
"""


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


def _copy_example_plugin(root: Path, name: str) -> Path:
    """Copy the shipped reference plugin so the fixture is a real manifest."""
    target = root / name
    shutil.copytree(EXAMPLE_PLUGIN, target)
    return target


def _append_manifest_line(plugin_dir: Path, line: str) -> None:
    manifest = plugin_dir / "manifest.yaml"
    manifest.write_text(manifest.read_text(encoding="utf-8") + line, encoding="utf-8")


def _write_good_plugin(root: Path, name: str) -> Path:
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "manifest.yaml").write_text(
        f"name: {name}\nversion: 1.0.0\n", encoding="utf-8"
    )
    (plugin_dir / "plugin.py").write_text(GOOD_PLUGIN, encoding="utf-8")
    return plugin_dir


class TestInvalidManifestIsRefused:
    @pytest.mark.asyncio
    async def test_unknown_key_refuses_the_plugin(self, registry, tmp_path):
        plugin_dir = _write_good_plugin(tmp_path, "typo_plugin")
        _append_manifest_line(plugin_dir, "min_core_verison: 1.0.0\n")

        loader = PluginLoader(tmp_path, registry)
        assert await loader.load_plugin(plugin_dir, initialize=False) is None

    @pytest.mark.asyncio
    async def test_refusal_names_the_offending_key(
        self, registry, tmp_path, monkeypatch
    ):
        from core.plugins import loader as loader_module

        messages: list[str] = []
        monkeypatch.setattr(
            loader_module.logger,
            "error",
            lambda msg, *args, **kwargs: messages.append(msg % args if args else msg),
        )

        plugin_dir = _write_good_plugin(tmp_path, "typo_named")
        _append_manifest_line(plugin_dir, "min_core_verison: 1.0.0\n")

        loader = PluginLoader(tmp_path, registry)
        await loader.load_plugin(plugin_dir, initialize=False)

        joined = "\n".join(messages)
        assert "min_core_verison" in joined
        assert "min_core_version" in joined

    @pytest.mark.asyncio
    @pytest.mark.parametrize("app_env", ["development", "production"])
    async def test_refused_in_every_environment(
        self, registry, tmp_path, monkeypatch, app_env
    ):
        monkeypatch.setenv("APP_ENV", app_env)
        plugin_dir = _write_good_plugin(tmp_path, f"typo_{app_env}")
        _append_manifest_line(plugin_dir, "permisions: {}\n")

        loader = PluginLoader(tmp_path, registry)
        assert await loader.load_plugin(plugin_dir, initialize=False) is None

    @pytest.mark.asyncio
    async def test_unparseable_yaml_refuses_the_plugin(self, registry, tmp_path):
        plugin_dir = _write_good_plugin(tmp_path, "broken_yaml")
        (plugin_dir / "manifest.yaml").write_text("name: [unclosed\n", encoding="utf-8")

        loader = PluginLoader(tmp_path, registry)
        assert await loader.load_plugin(plugin_dir, initialize=False) is None

    @pytest.mark.asyncio
    async def test_no_manifest_at_all_still_loads(self, registry, tmp_path):
        """The legacy shape stays allowed — absence grants nothing either way."""
        plugin_dir = tmp_path / "manifestless"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.py").write_text(GOOD_PLUGIN, encoding="utf-8")

        loader = PluginLoader(tmp_path, registry)
        plugin = await loader.load_plugin(plugin_dir, initialize=False)
        assert plugin is not None
        assert type(plugin).__name__ == "GoodPlugin"


class TestRefusalIsTracked:
    """The refusal must be *recorded*, not just logged.

    The first cut called ``transition_to_failed`` before any
    ``transition_to_loading``, and lifecycle's FAILED transition indexed
    ``self._metadata[plugin_name]`` directly (every sibling uses
    ``setdefault``). On the production wiring — where lifespan always attaches a
    lifecycle manager — the actionable log line was followed by a spurious
    ``KeyError`` traceback, and the FAILED transition was half-applied: state
    set, but no reason, hooks or metrics. ``/plugins`` then showed FAILED with
    nothing to act on.
    """

    @pytest.mark.asyncio
    async def test_refusal_records_failed_with_a_reason(self, registry, tmp_path):
        from core.plugins.lifecycle import PluginLifecycleManager, PluginState

        lifecycle = PluginLifecycleManager()
        plugin_dir = _write_good_plugin(tmp_path, "tracked_typo")
        _append_manifest_line(plugin_dir, "min_core_verison: 1.0.0\n")

        loader = PluginLoader(tmp_path, registry, lifecycle_manager=lifecycle)

        # Must not raise: a KeyError here is the defect.
        assert await loader.load_plugin(plugin_dir, initialize=False) is None

        assert lifecycle.get_state("tracked_typo") is PluginState.FAILED
        metadata = lifecycle.get_plugin_metadata("tracked_typo") or {}
        assert "failed_at" in metadata
        assert "min_core_verison" in metadata.get("error", "")
        # LOADING must precede FAILED: transition_to_loading *replaces* the
        # metadata dict, so a refusal recorded before it would have its reason
        # wiped — and an on_before_load hook consumer would see a plugin fail
        # that it never saw start.
        assert "load_started_at" in metadata

    @pytest.mark.asyncio
    async def test_failed_transition_works_for_an_untracked_plugin(self):
        """lifecycle.transition_to_failed must not need prior tracking."""
        from core.plugins.lifecycle import PluginLifecycleManager, PluginState

        lifecycle = PluginLifecycleManager()
        await lifecycle.transition_to_failed("never-seen", ValueError("boom"))

        assert lifecycle.get_state("never-seen") is PluginState.FAILED
        assert (lifecycle.get_plugin_metadata("never-seen") or {}).get(
            "error"
        ) == "boom"

    @pytest.mark.asyncio
    async def test_unloading_transition_works_for_an_untracked_plugin(self):
        """Same latent KeyError one method down; fixed with it."""
        from core.plugins.lifecycle import PluginLifecycleManager, PluginState

        lifecycle = PluginLifecycleManager()
        await lifecycle.transition_to_unloading("never-seen")
        assert lifecycle.get_state("never-seen") is PluginState.UNLOADING

    @pytest.mark.asyncio
    async def test_valid_plugin_still_reaches_loaded(self, registry, tmp_path):
        from core.plugins.lifecycle import PluginLifecycleManager, PluginState

        lifecycle = PluginLifecycleManager()
        plugin_dir = _write_good_plugin(tmp_path, "tracked_ok")

        loader = PluginLoader(tmp_path, registry, lifecycle_manager=lifecycle)
        assert await loader.load_plugin(plugin_dir, initialize=False) is not None
        assert lifecycle.get_state("tracked_ok") is PluginState.LOADED


class TestPartialFailure:
    @pytest.mark.asyncio
    async def test_one_broken_manifest_does_not_abort_the_tree(
        self, registry, tmp_path
    ):
        """A copied example-plugin with an unknown key must not stop the rest.

        ``plugin.metadata`` re-parses the manifest and now raises
        ``ManifestValidationError``; before the fix that exception escaped
        ``load_all_plugins`` and no plugin at all was loaded.
        """
        broken = _copy_example_plugin(tmp_path, "broken_example")
        _append_manifest_line(broken, "not_a_real_key: 1\n")
        _write_good_plugin(tmp_path, "healthy_one")
        _write_good_plugin(tmp_path, "healthy_two")

        loader = PluginLoader(tmp_path, registry)
        loaded = await loader.load_all_plugins(activate_on_load=False)

        assert loaded == 2
        assert sorted(p.metadata.name for p in registry.get_all()) == [
            "healthy_one",
            "healthy_two",
        ]

    @pytest.mark.asyncio
    async def test_metadata_raising_mid_pass_is_contained(
        self, registry, tmp_path, monkeypatch
    ):
        """Even an unexpected raise from the per-plugin body is contained."""
        _write_good_plugin(tmp_path, "alpha_ok")
        _write_good_plugin(tmp_path, "beta_ok")

        from core.plugins import bulk_load

        real = bulk_load._instantiate_one
        calls: list[str] = []

        async def _explode_once(loader, plugin_dir, configs, **kwargs):
            calls.append(plugin_dir.name)
            if plugin_dir.name == "alpha_ok":
                raise RuntimeError("manifest gremlin")
            return await real(loader, plugin_dir, configs, **kwargs)

        monkeypatch.setattr(bulk_load, "_instantiate_one", _explode_once)

        loader = PluginLoader(tmp_path, registry)
        loaded = await loader.load_all_plugins(activate_on_load=False)

        assert sorted(calls) == ["alpha_ok", "beta_ok"]
        assert loaded == 1
