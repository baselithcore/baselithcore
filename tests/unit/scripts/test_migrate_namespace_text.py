"""Text, Markdown and targeted rules of the namespace codemod."""

from __future__ import annotations

import pytest

pytest.importorskip("libcst")

from scripts.namespace_migration.config import (
    INTERNAL_DOC,
    RUNTIME,
    TEST,
    TOOLING,
    USER,
    classify,
    is_excluded,
)
from scripts.namespace_migration.files import shadow_roots, shadowed_for
from scripts.namespace_migration.index import ModuleIndex
from scripts.namespace_migration.markdown import rewrite_markdown
from scripts.namespace_migration.targeted import apply_targeted
from scripts.namespace_migration.text_rules import TextContext, rewrite_text

TREE = [
    "core/__init__.py",
    "core/_version.py",
    "core/cli/__main__.py",
    "core/db/migrations/env.py",
    "core/services/__init__.py",
    "core/services/sandbox/Dockerfile.sandbox",
    "plugins/__init__.py",
    "plugins/goals/manifest.yaml",
    "plugins/example-plugin/plugin.py",
    "mkdocs-site/docs/plugins/packaging.md",
    "mkdocs-site/docs/guide.md",
]
INDEX = ModuleIndex.from_files(TREE)


def txt(text: str, cls: str = TOOLING, local_dir: str | None = None) -> str:
    return rewrite_text(
        text, TextContext(index=INDEX, file_class=cls, local_dir=local_dir)
    )


class TestClassification:
    @pytest.mark.parametrize(
        ("rel", "cls"),
        [
            ("core/x.py", RUNTIME),
            ("plugins/goals/plugin.py", RUNTIME),
            ("plugins/goals/tests/test_x.py", TEST),
            ("plugins/goals/README.md", INTERNAL_DOC),
            ("tests/unit/test_x.py", TEST),
            ("scripts/check.py", TOOLING),
            ("pyproject.toml", TOOLING),
            ("CLAUDE.md", INTERNAL_DOC),
            (".claude/skills/x/SKILL.md", INTERNAL_DOC),
            ("mkdocs-site/docs/guide.md", USER),
            ("templates/app/backend.py", USER),
            ("README.md", USER),
        ],
    )
    def test_classify(self, rel: str, cls: str) -> None:
        assert classify(rel) == cls

    @pytest.mark.parametrize(
        "rel",
        [
            "CHANGELOG.md",
            "plugins/x/CHANGELOG.md",
            "docs/superpowers/plans/p.md",
            "plugins/x/ui/dist/a.js",
            "plugins/x/ui/node_modules/y/index.js",
            "scripts/namespace_migration/config.py",
            "uv.lock",
        ],
    )
    def test_excluded(self, rel: str) -> None:
        assert is_excluded(rel)

    def test_shadow_only_for_import_roots(self) -> None:
        files = [
            "vendor/src/core/__init__.py",
            "vendor/src/app.py",
            "tests/__init__.py",
            "tests/core/__init__.py",
            "pkg/__init__.py",
            "pkg/core/__init__.py",
        ]
        roots = shadow_roots(files)
        assert shadowed_for("vendor/src/app.py", roots) == frozenset({"core"})
        assert shadowed_for("tests/core/test_x.py", roots) == frozenset()
        assert shadowed_for("pkg/x.py", roots) == frozenset()


class TestTextRules:
    def test_dotted_resolved(self) -> None:
        assert txt("command: python -m core.cli.__main__") == (
            "command: python -m baselith.core.cli.__main__"
        )

    def test_paths(self) -> None:
        assert txt("COPY core/ core/\n") == "COPY baselith/core/ baselith/core/\n"
        assert txt("script_location = core/db/migrations") == (
            "script_location = baselith/core/db/migrations"
        )

    def test_relative_dots(self) -> None:
        assert (
            txt("see ../../core/cli/__main__.py")
            == "see ../../baselith/core/cli/__main__.py"
        )

    def test_already_migrated_untouched(self) -> None:
        src = "baselith/core/x baselith.core.cli ./baselith/plugins"
        assert txt(src) == src

    def test_bare_plugins_dir_tooling_vs_user(self) -> None:
        assert txt("plugins/*/ui/src") == "baselith/plugins/*/ui/src"
        assert txt("drop it under plugins/", cls=USER) == "drop it under plugins/"
        assert txt("lives under `plugins/`", cls=INTERNAL_DOC) == (
            "lives under `baselith/plugins/`"
        )

    def test_unknown_plugin_path_in_user_docs_kept(self) -> None:
        assert txt("plugins/my_plugin/manifest.yaml", cls=USER) == (
            "plugins/my_plugin/manifest.yaml"
        )

    def test_runtime_plugin_path_moves_independent_of_tree(self) -> None:
        assert txt("plugins/auth/x.py", cls=RUNTIME) == "baselith/plugins/auth/x.py"

    def test_path_inside_a_regex(self) -> None:
        assert txt(r"Path\(['\"]core/_version\.py['\"]\)") == (
            r"Path\(['\"]baselith/core/_version\.py['\"]\)"
        )

    def test_json_escaped_quotes(self) -> None:
        assert txt('"cmd": "Path(\\"core/_version.py\\")"') == (
            '"cmd": "Path(\\"baselith/core/_version.py\\")"'
        )

    def test_rooted(self) -> None:
        ctx = TextContext(index=INDEX, file_class=TOOLING, rooted_paths=True)
        assert rewrite_text("/core/ @team\n", ctx) == "/baselith/core/ @team\n"

    def test_rooted_is_an_http_route_outside_ignore_files(self) -> None:
        src = 'GET "/plugins/goals/static/app.js"'
        assert txt(src, cls=RUNTIME) == src
        assert txt(src, cls=TEST) == src

    def test_url(self) -> None:
        assert txt("https://github.com/o/r/blob/main/core/cli/__main__.py") == (
            "https://github.com/o/r/blob/main/baselith/core/cli/__main__.py"
        )

    def test_dot_dir(self) -> None:
        assert txt("- ./plugins:/app/plugins") == "- ./baselith/plugins:/app/plugins"

    def test_unrelated(self) -> None:
        src = "docker-compose.core.yml en_core_web_sm hardcore/x postgres_core_data plugins.yaml"
        assert txt(src) == src

    def test_keep_pragma_line(self) -> None:
        src = "core/x  # ns-migrate: keep\ncore/cli\n"
        assert txt(src) == "core/x  # ns-migrate: keep\nbaselith/core/cli\n"

    def test_markdown_local_link_kept(self) -> None:
        src = (
            "[pkg](plugins/packaging.md) and [repo](../../plugins/goals/manifest.yaml)"
        )
        out = txt(src, cls=USER, local_dir="mkdocs-site/docs")
        assert "(plugins/packaging.md)" in out
        assert "(../../baselith/plugins/goals/manifest.yaml)" in out


class TestMarkdown:
    def test_python_fence_through_libcst(self) -> None:
        md = "Intro core.cli\n\n```python\nimport core.services as s\nfrom plugins.goals import G\n```\n"
        out = rewrite_markdown(
            md, TextContext(index=INDEX, file_class=USER), "docs/a.md"
        )
        assert "import baselith.core.services as s" in out
        assert "from baselith.plugins.goals import G" in out
        assert "Intro baselith.core.cli" in out

    def test_unparsable_fence_falls_back_to_regex(self) -> None:
        md = "```python\nfrom core.services import x\n...\nclass A(\n```\n"
        out = rewrite_markdown(
            md, TextContext(index=INDEX, file_class=USER), "docs/a.md"
        )
        assert "from baselith.core.services import x" in out

    def test_indented_fence(self) -> None:
        md = "1. step\n\n    ```python\n    from core import x\n    ```\n"
        out = rewrite_markdown(
            md, TextContext(index=INDEX, file_class=USER), "docs/a.md"
        )
        assert "    from baselith.core import x\n" in out

    def test_in_page_anchor_follows_a_rewritten_heading(self) -> None:
        md = "See [cfg](#governance-coreclicli).\n\n## Governance (core.cli.cli)\n"
        idx = ModuleIndex.from_files([*TREE, "core/cli/cli.py"])
        out = rewrite_markdown(md, TextContext(index=idx, file_class=USER), "docs/a.md")
        assert "## Governance (baselith.core.cli.cli)" in out
        assert "[cfg](#governance-baselithcoreclicli)" in out


class TestTargeted:
    def _apply(self, text: str, rel: str) -> str:
        ctx = TextContext(index=INDEX, file_class=TOOLING)
        return rewrite_text(apply_targeted(text, rel, ctx), ctx)

    def test_pyproject(self) -> None:
        src = (
            'include = ["baselith*", "core*", "plugins*"]\n'
            'exclude = ["plugins.example-plugin", "plugins.example-plugin.*"]\n'
            'baselith = "core.cli.__main__:main"\n'
            'version = {attr = "core._version.__version__"}\n'
            '"core.services.sandbox" = ["Dockerfile.sandbox"]\n'
            '"core/db/migrations/**" = ["E402"]\n'
        )
        out = self._apply(src, "pyproject.toml")
        assert 'include = ["baselith*"]' in out
        assert '"baselith.plugins.example-plugin.*"' in out
        assert '"baselith.core.cli.__main__:main"' in out
        assert '"baselith.core._version.__version__"' in out
        assert '"baselith.core.services.sandbox"' in out
        assert '"baselith/core/db/migrations/**"' in out

    def test_pre_commit(self) -> None:
        src = (
            "entry: bandit -r core plugins sdk scripts -ll\n"
            "entry: mypy core baselith --fast\n"
            "files: ^(core|baselith)/.*\\.pyi?$\n"
            "files: ^core/db/migrations/\n"
        )
        out = self._apply(src, ".pre-commit-config.yaml")
        assert "bandit -r baselith sdk scripts" in out
        assert "mypy baselith --fast" in out
        assert "files: ^baselith/.*" in out
        assert "files: ^baselith/core/db/migrations/" in out

    def test_pytest_cov(self) -> None:
        out = self._apply("    --cov=core\n    --cov=baselith\n", "pytest.ini")
        assert "--cov=baselith/core\n" in out
        assert "--cov=baselith\n" in out

    def test_container_paths(self) -> None:
        out = self._apply("RUN compileall /app/core /app/plugins\n", "Dockerfile")
        assert "/app/baselith/core /app/baselith/plugins" in out

    def test_idempotent(self) -> None:
        once = self._apply(
            'include = ["baselith*", "core*", "plugins*"]\n--cov=core\n',
            "pyproject.toml",
        )
        assert self._apply(once, "pyproject.toml") == once
