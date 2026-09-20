"""The ``baselith`` public facade: contract, laziness and gate coverage.

``baselith`` is the name downstream programs import. Three properties have to
hold for that promise to mean anything, and none of them are visible in a
normal feature test:

- every advertised name resolves (a typo in the lazy table is otherwise a
  runtime ``AttributeError`` in someone else's program);
- ``__all__`` and the lazy table agree, so the public API surface gate — which
  reads the literal ``__all__`` with the AST and never imports — snapshots the
  real surface;
- importing the package stays cheap, which is the entire point of resolving
  attributes lazily.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

import baselith

REPO_ROOT = Path(__file__).resolve().parents[2]
FACADE_INIT = REPO_ROOT / "baselith" / "__init__.py"


class TestFacadeContract:
    """What the facade promises to export."""

    def test_every_exported_name_resolves(self) -> None:
        """No advertised name is a typo in the lazy table."""
        unresolved = []
        for name in baselith.__all__:
            try:
                getattr(baselith, name)
            except (AttributeError, ImportError) as exc:  # pragma: no cover
                unresolved.append(f"{name}: {exc}")
        assert not unresolved, f"unresolvable exports: {unresolved}"

    def test_all_matches_exports(self) -> None:
        """``__all__`` is spelled out; this is what keeps it honest."""
        assert set(baselith.__all__) == set(baselith._EXPORTS) | {"__version__"}

    def test_all_is_sorted_and_unique(self) -> None:
        """A sorted literal keeps the gate's diffs readable."""
        assert baselith.__all__ == sorted(set(baselith.__all__))

    def test_unknown_attribute_raises_attribute_error(self) -> None:
        """The lazy hook must not turn a typo into a confusing ImportError."""
        with pytest.raises(AttributeError, match="no attribute 'NotAThing'"):
            baselith.NotAThing

    def test_dir_lists_the_public_names(self) -> None:
        """``dir()`` and tab-completion see names never yet accessed."""
        assert set(dir(baselith)) >= set(baselith.__all__)

    def test_version_is_the_single_source_of_truth(self) -> None:
        """The facade re-exports the version, it does not restate it."""
        from core._version import __version__ as core_version

        assert baselith.__version__ == core_version

    def test_ships_a_py_typed_marker(self) -> None:
        """Without the marker, type checkers ignore the package's annotations."""
        assert (REPO_ROOT / "baselith" / "py.typed").is_file()


class TestFacadeIsGateCovered:
    """The public API surface gate must be able to read this package."""

    def test_all_is_a_literal_list_of_strings(self) -> None:
        """A computed ``__all__`` is invisible to the AST-based gate."""
        tree = ast.parse(FACADE_INIT.read_text(encoding="utf-8"))
        assignments = [
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
        ]
        assert len(assignments) == 1, "expected exactly one __all__ assignment"
        value = assignments[0].value
        assert isinstance(value, ast.List)
        assert all(
            isinstance(element, ast.Constant) and isinstance(element.value, str)
            for element in value.elts
        )

    def test_recorded_in_the_public_api_baseline(self) -> None:
        """The facade is part of the frozen surface, not an unwatched extra."""
        import json

        baseline = json.loads(
            (REPO_ROOT / "scripts" / "public_api_baseline.json").read_text(
                encoding="utf-8"
            )
        )
        assert baseline["packages"]["baselith"] == sorted(baselith.__all__)


class TestFacadeIsLazy:
    """Importing the facade must not import the framework."""

    def test_import_pulls_in_no_core_subpackage(self) -> None:
        """A fresh interpreter importing ``baselith`` loads no ``core.*`` engine.

        Run out-of-process: this test session has already imported half the
        framework, so an in-process ``sys.modules`` check would prove nothing.
        """
        probe = (
            "import sys, baselith; "
            "print(sorted(m for m in sys.modules if m.startswith('core.') "
            "and m != 'core._version'))"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            timeout=90,
            check=True,
        )
        assert result.stdout.strip().endswith("[]"), (
            f"import baselith eagerly imported: {result.stdout.strip()}"
        )

    def test_accessing_a_name_caches_it_in_globals(self) -> None:
        """Second access is a dict lookup, not another import."""
        module_globals = vars(baselith)
        module_globals.pop("SkillResult", None)
        assert "SkillResult" not in module_globals
        first = baselith.SkillResult
        assert module_globals["SkillResult"] is first
