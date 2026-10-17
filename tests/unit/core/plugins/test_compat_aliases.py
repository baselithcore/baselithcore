"""Legacy module paths keep resolving after a plugin module moves."""

from __future__ import annotations

import importlib
import sys
import types

import pytest

from baselith.core.plugins import compat_aliases
from baselith.core.plugins.compat_aliases import (
    register_module_aliases,
    registered_aliases,
)


@pytest.fixture
def fake_package(monkeypatch: pytest.MonkeyPatch) -> str:
    """A throwaway package ``fakepkg`` with ``fakepkg.sub.real`` on sys.modules."""
    pkg = types.ModuleType("fakepkg")
    pkg.__path__ = []  # type: ignore[attr-defined]
    sub = types.ModuleType("fakepkg.sub")
    sub.__path__ = []  # type: ignore[attr-defined]
    real = types.ModuleType("fakepkg.sub.real")
    real.answer = 42  # type: ignore[attr-defined]
    for mod in (pkg, sub, real):
        monkeypatch.setitem(sys.modules, mod.__name__, mod)
    monkeypatch.setattr(compat_aliases, "_ALIASES", dict(compat_aliases._ALIASES))
    return "fakepkg"


def test_old_path_imports_the_moved_module_with_one_identity(fake_package: str) -> None:
    register_module_aliases(
        {"real": "sub.real"}, packages=(fake_package, "legacy.alias")
    )
    sys.modules.setdefault("legacy", types.ModuleType("legacy"))
    legacy = types.ModuleType("legacy.alias")
    legacy.__path__ = []  # type: ignore[attr-defined]
    sys.modules["legacy.alias"] = legacy
    try:
        via_plugin = importlib.import_module("fakepkg.real")
        via_core = importlib.import_module("legacy.alias.real")
        assert via_plugin is sys.modules["fakepkg.sub.real"]
        assert via_core is via_plugin
        assert via_plugin.answer == 42
        assert via_plugin.__name__ == "fakepkg.sub.real"
    finally:
        for name in ("fakepkg.real", "legacy.alias.real", "legacy.alias", "legacy"):
            sys.modules.pop(name, None)


def test_unregistered_names_still_raise(fake_package: str) -> None:
    register_module_aliases({"real": "sub.real"}, packages=(fake_package,))
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("fakepkg.typo")
    sys.modules.pop("fakepkg.real", None)


def test_registry_lists_every_served_name(fake_package: str) -> None:
    register_module_aliases({"real": "sub.real"}, packages=(fake_package, "other.name"))
    table = registered_aliases()
    assert table["fakepkg.real"] == "fakepkg.sub.real"
    assert table["other.name.real"] == "fakepkg.sub.real"


def test_requires_a_package() -> None:
    with pytest.raises(ValueError):
        register_module_aliases({"a": "b"}, packages=())


@pytest.mark.parametrize(
    "old, new",
    [
        (
            "baselith.core.doc_sources.web",
            "baselith.plugins.document_sources.sources.web",
        ),
        (
            "baselith.core.scraper.crawler",
            "baselith.plugins.web_scraper.engine.crawler",
        ),
        (
            "baselith.plugins.api_routers.chat",
            "baselith.plugins.api_routers.routes.chat",
        ),
        (
            "baselith.plugins.reasoning_agent.reasoning_agent",
            "baselith.plugins.reasoning_agent.agent",
        ),
        ("baselith.plugins.goals.tracker", "baselith.plugins.goals.tracking.tracker"),
    ],
)
def test_framework_plugins_keep_their_documented_paths(old: str, new: str) -> None:
    assert importlib.import_module(old) is importlib.import_module(new)


def test_unchanged_modules_resolve_to_one_object_under_the_legacy_name(
    fake_package: str,
) -> None:
    sys.modules.setdefault("legacy2", types.ModuleType("legacy2"))
    legacy = types.ModuleType("legacy2.alias")
    legacy.__path__ = []  # type: ignore[attr-defined]
    sys.modules["legacy2.alias"] = legacy
    register_module_aliases(
        {}, packages=(fake_package, "legacy2.alias"), unchanged=("sub",)
    )
    try:
        assert (
            importlib.import_module("legacy2.alias.sub") is sys.modules["fakepkg.sub"]
        )
        assert "fakepkg.sub" not in registered_aliases()  # never aliased to itself
    finally:
        for name in ("legacy2.alias.sub", "legacy2.alias", "legacy2"):
            sys.modules.pop(name, None)


def test_core_compat_packages_share_every_module_with_the_plugin() -> None:
    import baselith.core.doc_sources.models as a
    import baselith.core.scraper.models as b
    import baselith.plugins.document_sources.models as a2
    import baselith.plugins.web_scraper.models as b2

    assert a is a2 and b is b2
