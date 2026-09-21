"""The lazy export helper, and the packages that use it.

:mod:`core._lazy` is what lets a package advertise a wide ``__all__`` without
charging every importer for all of it. Two things have to hold for that to be
safe: the resolver must behave exactly like the eager imports it replaced,
including their side effect of binding submodules, and each package's lazy
table must still describe its whole public surface.
"""

from __future__ import annotations

import importlib

import pytest

import core.orchestration
import core.plugins
from core._lazy import lazy_exports

LAZY_PACKAGES = (core.plugins, core.orchestration)


class TestLazyExports:
    """The resolver itself."""

    def test_resolves_a_name_from_its_submodule(self) -> None:
        getter = lazy_exports("core.plugins", {"SkillResult": "result"})

        from core.plugins.result import SkillResult

        assert getter("SkillResult") is SkillResult

    def test_caches_the_value_on_the_package(self) -> None:
        """Second read must be a dict lookup, not another import."""
        import sys

        package = sys.modules["core.plugins"]
        vars(package).pop("PluginHealth", None)
        getter = lazy_exports("core.plugins", {"PluginHealth": "health"})

        resolved = getter("PluginHealth")

        assert vars(package)["PluginHealth"] is resolved

    def test_supports_a_renamed_export(self) -> None:
        """``from .api import router as plugin_management_router``."""
        getter = lazy_exports("core.plugins", {"the_router": "api:router"})

        from core.plugins.api import router

        assert getter("the_router") is router

    def test_falls_back_to_a_submodule(self) -> None:
        """Eager imports bound submodules as attributes; that must survive."""
        getter = lazy_exports("core.plugins", {})

        assert getter("result") is importlib.import_module("core.plugins.result")

    def test_unknown_name_raises_attribute_error(self) -> None:
        getter = lazy_exports("core.plugins", {})

        with pytest.raises(AttributeError, match="no attribute 'nope'"):
            getter("nope")


@pytest.mark.parametrize("package", LAZY_PACKAGES, ids=lambda p: p.__name__)
class TestLazyPackages:
    """What each converted package still owes its callers."""

    def test_lazy_package_exports(self, package) -> None:  # type: ignore[no-untyped-def]
        """The lazy table and ``__all__`` describe the same surface.

        They are two literals in one file. A name in ``__all__`` but not in
        the table raises AttributeError for anyone who imports it; a name in
        the table but not in ``__all__`` is invisible to the public API
        surface gate.
        """
        assert set(package._EXPORTS) == set(package.__all__)

    def test_every_exported_name_resolves(self, package) -> None:  # type: ignore[no-untyped-def]
        """A typo in the table is otherwise a runtime error in someone's code."""
        unresolved = []
        for name in package.__all__:
            try:
                getattr(package, name)
            except (AttributeError, ImportError) as exc:  # pragma: no cover
                unresolved.append(f"{name}: {exc}")

        assert not unresolved, f"{package.__name__}: {unresolved}"

    def test_dir_lists_the_public_names(self, package) -> None:  # type: ignore[no-untyped-def]
        assert set(dir(package)) >= set(package.__all__)

    def test_all_is_sorted_nowhere_but_readable(self, package) -> None:  # type: ignore[no-untyped-def]
        """``__all__`` is grouped by concern, so only uniqueness is enforced."""
        assert len(package.__all__) == len(set(package.__all__))
