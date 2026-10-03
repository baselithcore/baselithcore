"""
Init command - Create new projects from templates.
"""

import argparse
import re
import shutil
from pathlib import Path
from typing import cast

from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt

from core import __version__ as FRAMEWORK_VERSION
from core.cli.ui import console, print_error, print_panel, print_step, print_success

_AGENT_MODULE = '''"""The project's first agent.

Run it with ``python -m app.agent``. ``Agent.run`` is a coroutine, so it needs
a running event loop — hence ``asyncio.run`` at the bottom.
"""

import asyncio

from baselith import Agent


async def current_time(timezone: str = "UTC") -> str:
    """Return the current time in a timezone.

    Args:
        timezone: IANA timezone name, e.g. "Europe/Rome".
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(timezone)).isoformat()


agent = Agent(
    system_prompt="You are a concise assistant.",
    tools=[current_time],
)


async def main() -> None:
    """Ask the agent one question and print the answer."""
    result = await agent.run("What time is it in Europe/Rome?")
    print(result.text)


if __name__ == "__main__":
    asyncio.run(main())
'''

_AGENT_TEST_MODULE = '''"""The scaffolded agent is importable and declares its tool."""

from app.agent import agent


def test_agent_exposes_its_tool() -> None:
    assert "current_time" in agent.tool_names
'''

#: Templates the CLI can scaffold without a checkout of this repository.
#:
#: ``full`` and ``chat-only`` used to sit here with an empty ``files`` dict.
#: The scaffolder only checked that ``files`` *was* a dict, so each of them
#: created an empty directory and then printed "Created project at ..." — and
#: the interactive prompt offered both by name. They are removed rather than
#: stubbed: a template that produces nothing is worse than one that is not
#: offered. The richer starters (``rag-system``, ``multi-agent-collab``,
#: ``baselith-core-template``, ``custom-agent-template``) are directories
#: under ``templates/``, shipped in the wheel as ``core/cli/scaffold_templates``
#: and discovered at runtime by :func:`templates_root`.
PROJECT_TEMPLATES = {
    "minimal": {
        "description": "Minimal project: one agent, wired to the public API",
        "files": {
            "README.md": """# {project_name}

A BaselithCore project.

## Quick start

```bash
pip install -e .
python -m app.agent
```

## Layout

```
{project_name}/
├── app/           # Your application code; app/agent.py is the entry point
├── plugins/       # Your plugins — domain logic belongs here
├── tests/         # Your tests
├── .env           # Local configuration
└── pyproject.toml
```

The framework is a dependency (`baselith-core`), not a directory inside this
project. Import it as `baselith`:

```python
from baselith import Agent
```
""",
            "pyproject.toml": """[project]
name = "{project_name}"
version = "0.1.0"
description = "A BaselithCore project"
requires-python = ">=3.12"
dependencies = [
    "baselith-core>={framework_version}",
]

[project.optional-dependencies]
dev = ["pytest", "pytest-cov", "pytest-asyncio"]

[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["app*", "plugins*"]
""",
            ".env": """# {project_name} configuration

CORE_LOG_LEVEL=INFO
CORE_DEBUG=false

LLM_PROVIDER=ollama
LLM_MODEL=llama3.1:8b
""",
            ".gitignore": """__pycache__/
*.py[cod]
.venv/
.env
.pytest_cache/
""",
            "app/__init__.py": '"""Application module."""\n',
            "app/agent.py": _AGENT_MODULE,
            "plugins/.gitkeep": "",
            "tests/__init__.py": '"""Test module."""\n',
            "tests/test_agent.py": _AGENT_TEST_MODULE,
        },
    },
}


#: A directory under ``templates/`` is a *project* starter when it has a
#: README and something to run or install. The marker matters because that
#: tree also holds scaffolds that are not projects — ``plugin-template`` is a
#: plugin, ``backstage`` is a portal — and offering either as a project
#: template hands the operator a directory ``baselith run`` cannot start.
_PROJECT_MARKERS = ("pyproject.toml", "requirements.txt", "main.py", "agent.py")


def _is_project_template(directory: Path) -> bool:
    """Whether a ``templates/`` directory scaffolds a runnable project."""
    if not (directory / "README.md").is_file():
        return False
    return any((directory / marker).is_file() for marker in _PROJECT_MARKERS)


def available_templates() -> list[str]:
    """Every template this invocation can actually scaffold.

    The built-in ones always work; the directory ones come from
    :func:`templates_root` — the package an installed wheel carries, or the
    ``templates/`` tree of a checkout.

    Returns:
        Template names, built-ins first, each of which will produce a project.
    """
    names = list(PROJECT_TEMPLATES)
    templates_dir = templates_root()
    if templates_dir.is_dir():
        names.extend(
            sorted(
                entry.name
                for entry in templates_dir.iterdir()
                if entry.is_dir()
                and not entry.name.startswith(".")
                and entry.name not in PROJECT_TEMPLATES
                and _is_project_template(entry)
            )
        )
    return names


#: Where a wheel install carries the directory starters. ``templates/`` sits
#: outside every package, so ``build_support/scaffold_templates.py`` copies
#: the CLI's starters here at build time; a checkout has no such directory.
PACKAGED_TEMPLATES = Path(__file__).resolve().parents[1] / "scaffold_templates"
#: The ``templates/`` tree of the checkout this ``core`` was imported from
#: (an editable install); absent — and harmless — under ``site-packages``.
CHECKOUT_TEMPLATES = Path(__file__).resolve().parents[3] / "templates"


def templates_root() -> Path:
    """The directory the directory templates are read from.

    In order: the checkout around the cwd (a contributor scaffolding from the
    repository), the starters packaged in an installed wheel, then the
    checkout this ``core`` package was imported from (an editable install
    used from any directory). The returned path may not exist — callers then
    offer the built-in templates only.
    """
    in_cwd = find_project_root() / "templates"
    if in_cwd.is_dir():
        return in_cwd
    if PACKAGED_TEMPLATES.is_dir():
        return PACKAGED_TEMPLATES
    return CHECKOUT_TEMPLATES


def _next_steps(files: dict[str, str]) -> list[str]:
    """The install and run commands that fit what a template wrote.

    ``baselith run`` starts a server from ``backend.py``, so it is the right
    next step only for a template that scaffolds one; the panel used to name
    it unconditionally, then ``pip install -e .`` / ``python -m app.agent``
    for every template — wrong for a directory starter that has a
    ``requirements.txt`` and a ``main.py``.
    """
    steps: list[str] = []
    if "pyproject.toml" in files:
        steps.append("[bold]pip[/bold] install -e .")
    elif "requirements.txt" in files:
        steps.append("[bold]pip[/bold] install -r requirements.txt")
    if "backend.py" in files:
        steps.append("[bold]baselith[/bold] run")
    elif "app/agent.py" in files:
        steps.append("[bold]python[/bold] -m app.agent")
    elif "main.py" in files:
        steps.append("[bold]python[/bold] main.py")
    elif "agent.py" in files:
        steps.append("[bold]python[/bold] agent.py")
    return steps


def find_project_root() -> Path:
    """Find the root of the Baselith-Core project."""
    current = Path.cwd()
    # Check if we are in the root (look for pyproject.toml and core/)
    if (current / "core").is_dir() and (current / "templates").is_dir():
        return current

    # Try parent directories
    for parent in current.parents:
        if (parent / "core").is_dir() and (parent / "templates").is_dir():
            return parent

    return current


def is_valid_project_name(name: str) -> bool:
    """
    Validate project name to prevent path traversal and ensure valid identifiers.

    Args:
        name: Project name to validate

    Returns:
        True if valid, False otherwise

    Valid names:
    - Must be 1-64 characters
    - Can only contain letters, numbers, underscores, and hyphens
    - Must start with a letter or number
    - Cannot be reserved names (., .., -, etc.)
    """
    if not name or len(name) > 64:
        return False

    # Reject reserved names and path components
    if name in (".", "..", "-", "_"):
        return False

    # Must match: start with alphanumeric, then alphanumeric/underscore/hyphen
    # This prevents path traversal (../, ./, etc.) and ensures valid directory names
    pattern = r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$"
    return bool(re.match(pattern, name))


def run_init(project_name: str | None = None, template: str | None = None) -> int:
    """
    Create a new project from template.

    Args:
        project_name: Name of the project directory (prompts if None)
        template: Template to use (prompts if None)

    Returns:
        Exit code (0 for success)
    """
    if not project_name:
        p_name = Prompt.ask("[bold cyan]? What is your project named?[/bold cyan]")
        if not p_name:
            print_error("Project name is required.")
            return 1
        project_name = p_name

    # Type narrowing for mypy
    assert project_name is not None

    # Validate project name to prevent path traversal and ensure valid identifiers
    if not is_valid_project_name(project_name):
        print_error(
            f"Invalid project name '{project_name}'.\n"
            "Project names must:\n"
            "  • Be 1-64 characters long\n"
            "  • Start with a letter or number\n"
            "  • Contain only letters, numbers, underscores, and hyphens\n"
            "  • Not be reserved names (., .., -, _)"
        )
        return 1

    if not template:
        # Offer what actually exists. The list used to be a hardcoded five,
        # two of which scaffolded nothing and one of which ("baselith-core")
        # matched neither an internal template nor a directory, so three of
        # the five choices were dead ends.
        choices = available_templates()
        template = Prompt.ask(
            "[bold cyan]? Which template would you like to use?[/bold cyan]",
            choices=choices,
            default="minimal",
        )

    if not template:
        template = "minimal"

    # Satisfy mypy
    assert project_name is not None
    assert template is not None

    base_dir = Path.cwd()
    if (base_dir / "plugins").is_dir():
        base_dir = base_dir / "plugins"

    project_path = base_dir / project_name

    if project_path.exists():
        print_error(f"Directory '{project_name}' already exists")
        return 1

    # 1. Try to find template directory in project root
    templates_dir = templates_root()

    template_path = templates_dir / template

    files_to_create: dict[str, str] = {}

    if template_path.is_dir():
        print_step(
            f"Creating project '{project_name}' from template directory '{template}'..."
        )
        # Copy files from directory recursively
        for item in template_path.rglob("*"):
            rel_path = item.relative_to(template_path)
            # pip byte-compiles the templates it installs; never copy that.
            if "__pycache__" in rel_path.parts or item.suffix in (".pyc", ".pyo"):
                continue
            if item.is_file() and item.name != ".DS_Store":
                content = item.read_text()
                files_to_create[str(rel_path)] = content
    else:
        # 2. Fallback to hardcoded templates
        template_data = PROJECT_TEMPLATES.get(template)
        if not template_data or not isinstance(template_data.get("files"), dict):
            print_error(f"Unknown template or directory '{template}'")
            # List available templates
            console.print("\n[bold]Available internal templates:[/bold]")
            for internal_template_name in PROJECT_TEMPLATES:
                console.print(f"  [cyan]- {internal_template_name}[/cyan]")
            if templates_dir.is_dir():
                console.print("\n[bold]Available template directories:[/bold]")
                for template_dir in templates_dir.iterdir():
                    if template_dir.is_dir() and not template_dir.name.startswith("."):
                        console.print(f"  [cyan]- {template_dir.name}[/cyan]")
            return 1

        print_step(
            f"Creating project '{project_name}' with internal template '{template}'..."
        )
        files_to_create = cast(dict[str, str], template_data.get("files", {}))

    if not files_to_create:
        # A template that writes nothing used to create the directory anyway
        # and report success, leaving the operator to discover the emptiness.
        print_error(f"Template '{template}' contains no files; nothing was created.")
        return 1

    try:
        # Create project directory
        project_path.mkdir(parents=True)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task(
                f"[bold green]Scaffolding code for '{project_name}'...",
                total=len(files_to_create),
            )

            # Create files
            for file_path, content in files_to_create.items():
                full_path = project_path / file_path
                full_path.parent.mkdir(parents=True, exist_ok=True)

                # Replace template variables
                # Using .replace instead of .format to handle code files with many curly braces
                final_content = content.replace("{project_name}", project_name).replace(
                    "{framework_version}", FRAMEWORK_VERSION
                )

                full_path.write_text(final_content)
                progress.advance(task)

        print_success(f"Created project at [bold]{project_path}[/bold]")

        # ``project_path`` may have been redirected under ``plugins/`` when run
        # from the monorepo root, so derive the cd target relative to cwd.
        try:
            cd_target = project_path.relative_to(Path.cwd())
        except ValueError:
            cd_target = project_path

        steps = [f"[bold]cd[/bold] {cd_target}", *_next_steps(files_to_create)]
        next_steps = "\n".join(steps)
        print_panel(next_steps, title="Next steps", style="green")

        return 0

    except Exception as e:
        print_error(f"Error creating project: {e}")
        # Cleanup on failure
        if project_path.exists():
            shutil.rmtree(project_path)
        return 1


def register_parser(
    subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]",
    formatter_class: type[argparse.HelpFormatter],
) -> argparse.ArgumentParser:
    """Register 'init' command parser."""
    init_parser = subparsers.add_parser(
        "init",
        help="Bootstrap a new project",
        description="Bootstrap a new Baselith-Core project with a standardized directory structure and essential configurations.",
        formatter_class=formatter_class,
    )
    init_parser.add_argument(
        "project_name", nargs="?", help="The name of your new agentic system project"
    )
    # Derived from what this install can scaffold, never a hard-coded list:
    # the old one advertised templates that did not exist.
    choices = available_templates()
    init_parser.add_argument(
        "--template",
        choices=choices,
        help=f"Starter template: {', '.join(choices)}",
    )
    return init_parser


__all__ = ["register_parser", "run_init"]
