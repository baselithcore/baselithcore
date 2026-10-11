"""Python rules of the namespace codemod (scripts/namespace_migration)."""

from __future__ import annotations

import textwrap

import pytest

pytest.importorskip("libcst")

from scripts.namespace_migration.config import RUNTIME, TEST, TOOLING
from scripts.namespace_migration.index import ModuleIndex
from scripts.namespace_migration.py_rewrite import rewrite_python
from scripts.namespace_migration.text_rules import TextContext

TREE = [
    "core/__init__.py",
    "core/_version.py",
    "core/agent/__init__.py",
    "core/agent/base.py",
    "core/cli/__main__.py",
    "core/services/__init__.py",
    "core/services/llm.py",
    "core/static/index.html",
    "plugins/__init__.py",
    "plugins/goals/__init__.py",
    "plugins/goals/plugin.py",
]
INDEX = ModuleIndex.from_files(TREE)


def rw(src: str, rel: str = "core/x/mod.py", cls: str = RUNTIME, **kw: object) -> str:
    ctx = TextContext(index=INDEX, file_class=cls, **kw)  # type: ignore[arg-type]
    moves = rel.split("/")[0] in ("core", "plugins")
    return rewrite_python(textwrap.dedent(src), rel, ctx, moves=moves)


def ctx_of(src: str, rel: str = "core/x/mod.py", cls: str = RUNTIME) -> TextContext:
    ctx = TextContext(index=INDEX, file_class=cls)
    rewrite_python(
        textwrap.dedent(src), rel, ctx, moves=rel.startswith(("core/", "plugins/"))
    )
    return ctx


class TestImports:
    def test_from_import(self) -> None:
        assert (
            rw("from core.agent import Agent\n")
            == "from baselith.core.agent import Agent\n"
        )

    def test_from_top(self) -> None:
        assert rw("from core import agent\n") == "from baselith.core import agent\n"

    def test_from_plugins(self) -> None:
        assert rw("from plugins.goals.plugin import G\n") == (
            "from baselith.plugins.goals.plugin import G\n"
        )

    def test_import_as(self) -> None:
        assert (
            rw("import core.agent.base as b\n")
            == "import baselith.core.agent.base as b\n"
        )

    def test_bare_top_import_binds_same_name(self) -> None:
        assert rw("import core\n") == "from baselith import core\n"

    def test_bare_top_mixed_with_other_names(self) -> None:
        out = rw("import os, core\n")
        assert out == "import os; from baselith import core\n"

    def test_submodule_import_renames_uses(self) -> None:
        out = rw(
            """
            import core.services.llm

            def f():
                return core.services.llm.X
            """
        )
        assert "import baselith.core.services.llm" in out
        assert "return baselith.core.services.llm.X" in out

    def test_submodule_import_leaves_shadowing_local_alone(self) -> None:
        out = rw(
            """
            import core.services.llm

            def f(core):
                return core.attr
            """
        )
        assert "return core.attr" in out

    def test_relative_import_untouched(self) -> None:
        src = "from . import core\nfrom .core import x\nfrom ..plugins import y\n"
        assert rw(src) == src

    def test_from_other_package_importing_name_core(self) -> None:
        src = "from foo import core\nfrom foo import core as c\n"
        assert rw(src) == src

    def test_unrelated_prefix_untouched(self) -> None:
        src = "import corelib\nfrom plugins_extra import x\nimport tests.core.x\n"
        assert rw(src) == src

    def test_shadowed_tree_untouched(self) -> None:
        src = "from core.engine import run\nimport plugins.local\n"
        out = rw(src, shadowed=frozenset({"core", "plugins"}))
        assert out == src


class TestStrings:
    def test_mock_patch_target_resolved(self) -> None:
        out = rw('mock.patch("core.services.llm.build")\n')
        assert out == 'mock.patch("baselith.core.services.llm.build")\n'

    def test_import_module_string(self) -> None:
        out = rw(
            'importlib.import_module("core.agent")\n__import__("core.cli.__main__")\n'
        )
        assert '"baselith.core.agent"' in out
        assert '"baselith.core.cli.__main__"' in out

    def test_entry_point_string(self) -> None:
        assert (
            rw('EP = "core.cli.__main__:main"\n')
            == 'EP = "baselith.core.cli.__main__:main"\n'
        )

    def test_whole_literal_core_name_moves_even_if_hypothetical(self) -> None:
        assert (
            rw('JOB = "core.jobs.reindex"\n') == 'JOB = "baselith.core.jobs.reindex"\n'
        )

    def test_unresolved_core_name_in_prose_reported_not_rewritten(self) -> None:
        src = '"""Replaces the old core.vectorstore.labels module."""\n'
        ctx = ctx_of(src)
        assert ("unresolved_dotted", "core.vectorstore.labels") in ctx.unresolved
        assert rw(src) == src

    def test_resolved_beats_file_extension_heuristic(self) -> None:
        idx = ModuleIndex.from_files([*TREE, "core/db/__init__.py"])
        ctx = TextContext(index=idx, file_class=RUNTIME)
        assert rewrite_python('K = {"core.db": 1}\n', "core/s.py", ctx, moves=True) == (
            'K = {"baselith.core.db": 1}\n'
        )

    def test_csv_prefix_literal(self) -> None:
        assert (
            rw('DEFAULT = "core.,plugins."\n')
            == 'DEFAULT = "baselith.core.,baselith.plugins."\n'
        )

    def test_monkeypatch_setitem_sys_modules(self) -> None:
        out = rw('monkeypatch.setitem(sys.modules, "plugins", stub)\n')
        assert out == 'monkeypatch.setitem(sys.modules, "baselith.plugins", stub)\n'

    def test_plugin_namespace_moves_even_if_absent(self) -> None:
        out = rw('monkeypatch.setattr("plugins.auth.api.thing", 1)\n')
        assert '"baselith.plugins.auth.api.thing"' in out

    def test_plugin_non_module_words_kept(self) -> None:
        src = 'A = "plugins.config.auth.enabled"\nB = "see plugins.items()"\nC = "plugins.__file__"\n'
        assert rw(src) == src

    def test_file_names_kept(self) -> None:
        src = 'A = "plugins.yaml"\nB = "core.py"\nC = "configs/plugins.yaml"\n'
        assert rw(src) == src

    def test_prefix_literal(self) -> None:
        out = rw('ALLOWED = ["core.", "plugins."]\n')
        assert out == 'ALLOWED = ["baselith.core.", "baselith.plugins."]\n'

    def test_fstring_head(self) -> None:
        out = rw('fqn = f"plugins.{name}.plugin"\n')
        assert out == 'fqn = f"baselith.plugins.{name}.plugin"\n'

    def test_fstring_resolved_dotted(self) -> None:
        assert rw('m = f"core.services.{x}"\n') == 'm = f"baselith.core.services.{x}"\n'

    def test_docstring_prose(self) -> None:
        out = rw('"""See core.agent.base and core/services/llm.py."""\n')
        assert (
            out
            == '"""See baselith.core.agent.base and baselith/core/services/llm.py."""\n'
        )

    def test_comment(self) -> None:
        assert rw("x = 1  # uses core.agent\n") == "x = 1  # uses baselith.core.agent\n"

    def test_bytes_untouched(self) -> None:
        src = 'B = b"core.agent"\n'
        assert rw(src) == src

    def test_keep_pragma(self) -> None:
        src = 'OLD = "core.agent"  # ns-migrate: keep\nNEW = "core.agent"\n'
        assert (
            rw(src)
            == 'OLD = "core.agent"  # ns-migrate: keep\nNEW = "baselith.core.agent"\n'
        )

    def test_unrelated_words(self) -> None:
        src = 'S = "the core. plugins: hardcore.x en_core_web_sm docker-compose.core.yml"\n'
        assert rw(src) == src


class TestModuleNameContexts:
    def test_sys_modules(self) -> None:
        out = rw(
            'if "plugins" not in sys.modules:\n'
            '    sys.modules["plugins"] = types.ModuleType("plugins")\n'
            'p = sys.modules.get("plugins")\n'
        )
        assert out.count('"baselith.plugins"') == 4

    def test_dunder_package(self) -> None:
        assert (
            rw('pkg.__package__ = "plugins"\n')
            == 'pkg.__package__ = "baselith.plugins"\n'
        )

    def test_get_logger(self) -> None:
        assert (
            rw('log = logging.getLogger("core")\n')
            == 'log = logging.getLogger("baselith.core")\n'
        )

    def test_other_bare_literals_reported_kept(self) -> None:
        src = 'COMPONENT = "core"\nroot = Path("plugins")\n'
        assert rw(src) == src


class TestToolingRoots:
    def test_tuple_of_roots(self) -> None:
        out = rw(
            'ROOTS = ("core", "plugins", "scripts")\n', rel="scripts/g.py", cls=TOOLING
        )
        assert out == 'ROOTS = ("baselith/core", "baselith/plugins", "scripts")\n'

    def test_pathy_name(self) -> None:
        out = rw(
            'SCANNED_ROOT = "plugins"\nLABEL = "plugins"\n',
            rel="scripts/g.py",
            cls=TOOLING,
        )
        assert out == 'SCANNED_ROOT = "baselith/plugins"\nLABEL = "plugins"\n'

    def test_runtime_tuples_untouched(self) -> None:
        src = 'NAMES = ("core", "plugins")\n'
        assert rw(src) == src


class TestPathExpressions:
    def test_moved_file_climb_to_root_gains_a_level(self) -> None:
        src = 'R = Path(__file__).resolve().parents[2] / "configs"\n'
        out = rw(src, rel="core/api/factory.py")
        assert out == 'R = Path(__file__).resolve().parents[3] / "configs"\n'

    def test_moved_file_climb_then_core_unchanged(self) -> None:
        src = 'S = Path(__file__).resolve().parents[2] / "core" / "static"\n'
        assert rw(src, rel="core/api/factory.py") == src

    def test_moved_file_parent_chain(self) -> None:
        assert rw("R = Path(__file__).parent.parent\n", rel="core/api/factory.py") == (
            "R = Path(__file__).parent.parent\n"
        )
        src = "R = Path(__file__).parent.parent.parent\n"
        out = rw(src, rel="core/api/factory.py")
        assert out == "R = Path(__file__).parent.parent.parent.parent\n"

    def test_moved_file_dirname_chain(self) -> None:
        src = "R = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n"
        out = rw(src, rel="plugins/goals/plugin.py")
        assert out.count("os.path.dirname(") == 4

    def test_moved_file_inner_climb_untouched(self) -> None:
        src = 'S = Path(__file__).parent / "templates"\n'
        assert rw(src, rel="core/api/factory.py") == src

    def test_moved_root_name_then_core_join(self) -> None:
        src = (
            'ROOT = Path(__file__).resolve().parents[2]\nS = ROOT / "core" / "static"\n'
        )
        out = rw(src, rel="core/api/factory.py")
        assert "parents[3]" in out
        assert 'ROOT / "baselith" / "core" / "static"' in out

    def test_script_root_join(self) -> None:
        src = 'ROOT = Path(__file__).resolve().parents[1]\nC = ROOT / "core"\n'
        out = rw(src, rel="scripts/check_x.py", cls=TOOLING)
        assert 'C = ROOT / "baselith" / "core"' in out
        assert "parents[1]" in out

    def test_script_heuristic_root_param(self) -> None:
        src = 'def f(root):\n    return root / "plugins"\n'
        out = rw(src, rel="scripts/check_x.py", cls=TOOLING)
        assert 'root / "baselith" / "plugins"' in out

    def test_test_tmp_path_is_not_repo_root(self) -> None:
        src = 'def test(tmp_path):\n    (tmp_path / "plugins").mkdir()\n'
        assert rw(src, rel="tests/unit/test_x.py", cls=TEST) == src

    def test_test_file_climb_to_root(self) -> None:
        src = 'C = Path(__file__).parents[2] / "core"\n'
        out = rw(src, rel="tests/unit/test_x.py", cls=TEST)
        assert out == 'C = Path(__file__).parents[2] / "baselith" / "core"\n'

    def test_fixture_test_joins_any_dir(self) -> None:
        src = (
            "from scripts.check import run\n"
            "def test(tmp_path):\n"
            '    (tmp_path / "site" / "core" / "plugins").mkdir(parents=True)\n'
            '    (tmp_path / "core").mkdir()\n'
        )
        ctx = TextContext(index=INDEX, file_class=TEST, repo_fixture=True)
        out = rewrite_python(src, "tests/unit/test_gate.py", ctx, moves=False)
        assert (
            '(tmp_path / "site" / "baselith" / "core" / "plugins").mkdir(parents=True)'
            in out
        )
        assert '(tmp_path / "baselith" / "core").mkdir(parents=True)' in out
        ctx2 = TextContext(index=INDEX, file_class=TEST, repo_fixture=True)
        assert rewrite_python(out, "tests/unit/test_gate.py", ctx2, moves=False) == out

    def test_path_string_in_script(self) -> None:
        out = rw('P = ROOT / "core/services/llm.py"\n', rel="scripts/x.py", cls=TOOLING)
        assert '"baselith/core/services/llm.py"' in out
