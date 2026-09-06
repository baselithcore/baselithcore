"""Synthetic parent packages must behave like really-imported ones.

``ensure_parent_packages`` registers ``plugins`` / ``plugins.<name>`` in
``sys.modules`` by hand. Python's own import machinery also binds the child on
its parent package, and anything resolving a dotted path by attribute walk —
``monkeypatch.setattr("plugins.<name>.<mod>.<attr>", ...)`` above all — depends
on that binding. Without it a test that patches a plugin symbol fails with
``module 'plugins' has no attribute '<name>'`` whenever some earlier test
already triggered the synthetic registration.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from core.plugins._module_paths import ensure_parent_packages


@pytest.fixture
def plugin_tree(tmp_path: Path) -> Path:
    plugin_dir = tmp_path / "plugins" / "sample_plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "__init__.py").write_text("VALUE = 'from-init'\n")
    return plugin_dir


@pytest.fixture(autouse=True)
def _restore_sys_modules():
    saved = {
        name: sys.modules.get(name) for name in ("plugins", "plugins.sample_plugin")
    }
    saved_attr = getattr(sys.modules.get("plugins"), "sample_plugin", None)
    yield
    for name, module in saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module
    parent = sys.modules.get("plugins")
    if parent is not None:
        if saved_attr is None:
            parent.__dict__.pop("sample_plugin", None)
        else:
            parent.sample_plugin = saved_attr


def test_child_is_bound_on_the_parent_package(plugin_tree: Path) -> None:
    ensure_parent_packages("sample_plugin", plugin_tree)

    parent = sys.modules["plugins"]
    assert parent.sample_plugin is sys.modules["plugins.sample_plugin"]


def test_attribute_walk_resolves_the_dotted_path(plugin_tree: Path) -> None:
    """The shape `monkeypatch.setattr` uses to resolve a dotted target."""
    ensure_parent_packages("sample_plugin", plugin_tree)

    found: types.ModuleType = sys.modules["plugins"]
    for part in ("sample_plugin",):
        found = getattr(found, part)
    assert found.VALUE == "from-init"


def test_existing_parent_is_not_replaced(plugin_tree: Path) -> None:
    """A real, already-imported ``plugins`` package keeps its identity."""
    sentinel = types.ModuleType("plugins")
    sentinel.__path__ = [str(plugin_tree.parent)]
    sentinel.MARKER = "original"  # type: ignore[attr-defined]
    sys.modules["plugins"] = sentinel

    ensure_parent_packages("sample_plugin", plugin_tree)

    assert sys.modules["plugins"] is sentinel
    assert sentinel.MARKER == "original"  # type: ignore[attr-defined]
    assert sentinel.sample_plugin is sys.modules["plugins.sample_plugin"]  # type: ignore[attr-defined]
