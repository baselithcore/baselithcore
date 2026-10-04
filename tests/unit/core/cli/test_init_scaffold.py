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

    def test_help_offers_exactly_the_available_templates(self) -> None:
        import argparse

        from core.cli.commands.init import register_parser

        parser = argparse.ArgumentParser()
        init = register_parser(parser.add_subparsers(), argparse.HelpFormatter)
        action = next(a for a in init._actions if a.dest == "template")
        assert list(action.choices or []) == available_templates()
        for dead in ("full", "chat-only", "baselith-core"):
            assert dead not in (action.choices or [])

    def test_checkout_templates_found_outside_the_checkout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from core.cli.commands.init import templates_root

        checkout_templates = Path(__file__).resolve().parents[4] / "templates"
        monkeypatch.chdir(tmp_path)
        assert templates_root() == checkout_templates
        assert "rag-system" in available_templates()

    def test_wheel_install_finds_the_packaged_templates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A wheel ships the starters inside the package, not as ``templates/``."""
        from core.cli.commands import init

        packaged = tmp_path / "site" / "core" / "cli" / "scaffold_templates"
        (packaged / "rag-system").mkdir(parents=True)
        (packaged / "rag-system" / "README.md").write_text("# {project_name}\n")
        (packaged / "rag-system" / "main.py").write_text("print('hi')\n")
        # pip byte-compiles every .py it installs, templates included.
        (packaged / "rag-system" / "__pycache__").mkdir()
        (packaged / "rag-system" / "__pycache__" / "main.cpython-312.pyc").write_bytes(
            b"\xcb\x0d\x0d\x0a"
        )
        monkeypatch.setattr(init, "PACKAGED_TEMPLATES", packaged)
        monkeypatch.setattr(init, "CHECKOUT_TEMPLATES", tmp_path / "absent")
        work = tmp_path / "work"
        work.mkdir()
        monkeypatch.chdir(work)

        assert init.templates_root() == packaged
        assert init.available_templates() == ["minimal", "rag-system"]
        assert run_init(project_name="demo", template="rag-system") == 0
        assert (work / "demo" / "README.md").read_text() == "# demo\n"
        assert not (work / "demo" / "__pycache__").exists()

    def test_a_checkout_in_the_cwd_wins_over_the_package(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from core.cli.commands import init

        packaged = tmp_path / "pkg"
        packaged.mkdir()
        (tmp_path / "core").mkdir()
        (tmp_path / "templates").mkdir()
        monkeypatch.setattr(init, "PACKAGED_TEMPLATES", packaged)
        monkeypatch.chdir(tmp_path)

        assert init.templates_root() == tmp_path / "templates"


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        (
            PROJECT_TEMPLATES["minimal"]["files"],
            ["install -e .", "compose up -d", "pull llama3.2", "run", "-m app.agent"],
        ),
        (
            {"requirements.txt": "", "main.py": ""},
            ["-r requirements.txt", "pull llama3.2", "main.py"],
        ),
        ({"requirements.txt": "", "README.md": ""}, ["-r requirements.txt"]),
        (
            {"pyproject.toml": "", "backend.py": ""},
            ["install -e .", "pull llama3.2", "run"],
        ),
        ({"agent.py": ""}, ["agent.py"]),
    ],
)
def test_next_steps_fit_the_template(
    files: dict[str, str], expected: list[str]
) -> None:
    from core.cli.commands.init_setup import next_steps

    steps = next_steps(files)
    assert len(steps) == len(expected)
    for step, fragment in zip(steps, expected, strict=True):
        assert fragment in step


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


@pytest.mark.parametrize(
    "template", ["baselith-core-template", "multi-agent-collab", "rag-system"]
)
def test_the_requirements_template_is_rendered(
    template: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``requirements.txt.tmpl`` becomes a concrete ``requirements.txt``."""
    from packaging.requirements import Requirement

    monkeypatch.chdir(tmp_path)
    assert run_init(project_name="demo", template=template) == 0
    project = tmp_path / "demo"

    assert not list(project.rglob("*.tmpl"))
    lines = [
        line
        for line in (project / "requirements.txt").read_text().splitlines()
        if line and not line.startswith("#")
    ]
    framework = [Requirement(line) for line in lines if "baselith-core" in line]
    assert framework, lines
    assert all(str(req.specifier) == f">={FRAMEWORK_VERSION}" for req in framework)
