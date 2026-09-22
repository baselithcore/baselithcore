"""``baselith init`` produces a project that is real, valid and installable.

Three of the five templates the prompt offered were dead ends: ``full`` and
``chat-only`` carried an empty ``files`` dict, and the scaffolder checked only
that ``files`` *was* a dict — so each created an empty directory and printed
"Created project at ...". ``baselith-core`` matched neither a built-in nor a
directory under ``templates/`` and simply errored.

What ``minimal`` did produce was also not a BaselithCore project: it declared
fastapi, uvicorn and pydantic but **not** ``baselith-core``, pinned
``requires-python = ">=3.11"`` against a framework that needs 3.12, stamped the
framework's own version as the new project's, and documented a ``core/``
directory it never created.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

import pytest

from core import __version__ as FRAMEWORK_VERSION
from core.cli.commands.init import (
    PROJECT_TEMPLATES,
    available_templates,
    run_init,
)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Scaffold the minimal template into an empty directory."""
    monkeypatch.chdir(tmp_path)
    exit_code = run_init(project_name="demo_project", template="minimal")
    assert exit_code == 0
    return tmp_path / "demo_project"


class TestOnlyWorkingTemplatesAreOffered:
    def test_no_builtin_template_is_empty(self) -> None:
        for name, data in PROJECT_TEMPLATES.items():
            assert data["files"], f"{name} scaffolds nothing"

    def test_every_offered_template_can_produce_files(self) -> None:
        for name in available_templates():
            assert name in PROJECT_TEMPLATES or (Path("templates") / name).is_dir(), (
                f"{name} is offered but resolves to nothing"
            )

    def test_an_unknown_template_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)

        assert run_init(project_name="demo", template="baselith-core") == 1
        assert not (tmp_path / "demo").exists()


class TestGeneratedProject:
    def test_it_depends_on_the_framework(self, project: Path) -> None:
        metadata = tomllib.loads((project / "pyproject.toml").read_text())
        requires = metadata["project"]["dependencies"]

        assert any(spec.startswith("baselith-core") for spec in requires), requires
        assert FRAMEWORK_VERSION in " ".join(requires)

    def test_it_targets_a_python_the_framework_supports(self, project: Path) -> None:
        metadata = tomllib.loads((project / "pyproject.toml").read_text())

        assert metadata["project"]["requires-python"] == ">=3.12"

    def test_it_starts_at_its_own_version_not_the_frameworks(
        self, project: Path
    ) -> None:
        metadata = tomllib.loads((project / "pyproject.toml").read_text())

        assert metadata["project"]["version"] == "0.1.0"

    def test_the_entry_point_is_valid_python(self, project: Path) -> None:
        ast.parse((project / "app" / "agent.py").read_text())

    def test_the_entry_point_uses_the_public_api(self, project: Path) -> None:
        source = (project / "app" / "agent.py").read_text()

        assert "from baselith import Agent" in source
        assert "core." not in source

    def test_the_readme_describes_the_layout_that_exists(self, project: Path) -> None:
        readme = (project / "README.md").read_text()

        for named in ("app/", "plugins/", "tests/"):
            assert named in readme
            assert (project / named.rstrip("/")).exists()
        # The old README advertised a vendored framework directory.
        assert "core/          #" not in readme

    def test_the_scaffolded_test_reads_a_public_attribute(self, project: Path) -> None:
        """A starter project must not have to reach into a private dict."""
        from baselith import Agent

        source = (project / "tests" / "test_agent.py").read_text()
        assert "agent._tools" not in source
        assert hasattr(Agent(), "tool_names")


@pytest.mark.skipif(
    sys.version_info < (3, 12), reason="the scaffold targets Python 3.12+"
)
def test_the_scaffolded_agent_declares_its_tool(project: Path) -> None:
    """Run the generated test's assertion without installing the project."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "scaffolded_agent", project / "app" / "agent.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert "current_time" in module.agent.tool_names
