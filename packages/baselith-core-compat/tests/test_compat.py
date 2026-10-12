# ns-migrate: skip-file  (the tests exercise the legacy names on purpose)
"""baselith-core-compat: legacy ``core``/``plugins`` names alias the 1.0 modules."""

from __future__ import annotations

import importlib
import sys
import textwrap
import warnings
from collections.abc import Iterator
from pathlib import Path

import baselith_core_compat
import pytest
from baselith_core_compat import _finder


def _forget_legacy() -> None:
    for name in list(sys.modules):
        if name in ("core", "plugins") or name.startswith(("core.", "plugins.")):
            del sys.modules[name]


@pytest.fixture(autouse=True)
def compat() -> Iterator[None]:
    _forget_legacy()
    baselith_core_compat.uninstall()
    baselith_core_compat.install()
    yield
    baselith_core_compat.uninstall()
    _forget_legacy()


def test_legacy_module_is_the_same_object() -> None:
    import baselith.core._version as new

    with pytest.warns(DeprecationWarning, match="baselith.core"):
        old = importlib.import_module("core._version")
    assert old is new
    assert old.__name__ == "baselith.core._version"


def test_from_import_of_a_symbol() -> None:
    from baselith.core.plugins.result import SkillResult as New

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from core.plugins.result import (
            SkillResult as Old,  # type: ignore[import-not-found]
        )
    assert Old is New


def test_one_warning_per_legacy_name() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        importlib.import_module("core._version")
        importlib.import_module("core.plugins.result")
    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(deprecations) == 1


def test_rq_style_dotted_path_resolves() -> None:
    """A job queued before the upgrade names ``core.…``; it must still import."""
    from pkgutil import resolve_name

    import baselith.core.plugins.result as new

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        assert resolve_name("core.plugins.result:ok") is new.ok


def test_real_core_package_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project's own ``core`` (a Django app, say) is never shadowed."""
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "__init__.py").write_text("MARKER = 'project'\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    import core  # type: ignore[import-not-found]

    assert core.MARKER == "project"
    import baselith.core

    assert baselith.core.__name__ == "baselith.core"


def test_uninstalled_finder_means_no_alias() -> None:
    baselith_core_compat.uninstall()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("core._version")


def test_install_is_idempotent() -> None:
    baselith_core_compat.install()
    baselith_core_compat.install()
    assert sum(isinstance(f, _finder.LegacyAliasFinder) for f in sys.meta_path) == 1


LEGACY_PLUGIN = {
    "manifest.yaml": "name: legacy_plugin\nversion: 0.1.0\ndescription: written for 0.x\n",
    "helpers.py": "GREETING = 'hello from a 0.x plugin'\n",
    "plugin.py": textwrap.dedent(
        """
        from core.plugins import Plugin
        from core.plugins.result import ok
        from plugins.legacy_plugin.helpers import GREETING


        class LegacyPlugin(Plugin):
            def greet(self):
                return ok(GREETING)
        """
    ),
}


async def test_plugin_written_with_old_imports_loads(tmp_path: Path) -> None:
    """The real 1.0 loader loads a plugin that still says ``from core …``."""
    from baselith.core.plugins import PluginRegistry
    from baselith.core.plugins.loader import PluginLoader

    plugin_dir = tmp_path / "legacy_plugin"
    plugin_dir.mkdir()
    for name, text in LEGACY_PLUGIN.items():
        (plugin_dir / name).write_text(text)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        loader = PluginLoader(tmp_path, PluginRegistry())
        plugin = await loader.load_plugin(plugin_dir, initialize=False)
    assert plugin is not None
    assert type(plugin).__module__ == "baselith.plugins.legacy_plugin.plugin"
    result = plugin.greet()
    assert result.success and result.data == "hello from a 0.x plugin"
    # The legacy self-import and the 1.0 module name are one module.
    assert (
        sys.modules["plugins.legacy_plugin"]
        is sys.modules["baselith.plugins.legacy_plugin"]
    )
