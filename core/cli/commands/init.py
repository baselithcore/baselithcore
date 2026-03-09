"""
Init command - Create new projects from templates.
"""

import shutil
from pathlib import Path
from typing import cast

from core.cli.ui import print_error, print_success, print_step, print_panel, console
from rich.prompt import Prompt
from rich.progress import Progress, SpinnerColumn, TextColumn


PROJECT_TEMPLATES = {
    "minimal": {
        "description": "Minimal project with core dependencies only",
        "files": {
            "README.md": """# {project_name}

A Baselith-Core project.

## Quick Start

```bash
# Install dependencies
pip install -e .

# Run the server
baselith run
```

## Project Structure

```
{project_name}/
├── app/           # Application code
├── core/          # Core framework (from MAS)
├── plugins/       # Your custom plugins
├── configs/       # Configuration files
└── tests/         # Test files
```
""",
            "pyproject.toml": """[project]
name = "{project_name}"
version = "0.1.0"
description = "Baselith-Core project"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn>=0.32.0",
    "pydantic>=2.9.0",
    "pydantic-settings>=2.0.0",
]

[project.optional-dependencies]
dev = ["pytest", "pytest-cov", "pytest-asyncio"]

[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"
""",
            ".env": """# {project_name} Configuration
# Copy to .env and customize

CORE_LOG_LEVEL=INFO
CORE_DEBUG=false

LLM_PROVIDER=ollama
LLM_MODEL=llama3.1:8b
""",
            "app/__init__.py": '"""Application module."""\n',
            "plugins/.gitkeep": "",
            "tests/__init__.py": '"""Test module."""\n',
        },
    },
    "full": {
        "description": "Full project with all services configured",
        "files": {
            # Include minimal files plus more
        },
    },
    "chat-only": {
        "description": "Chat service only, minimal footprint",
        "files": {},
    },
}


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

    if not template:
        # Check available templates to offer in prompt
        choices = ["minimal", "full", "chat-only", "rag-system", "baselith-core"]
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
    root = find_project_root()
    templates_dir = root / "templates"

    template_path = templates_dir / template

    files_to_create: dict[str, str] = {}

    if template_path.is_dir():
        print_step(
            f"Creating project '{project_name}' from template directory '{template}'..."
        )
        # Copy files from directory recursively
        for item in template_path.rglob("*"):
            if item.is_file() and item.name != ".DS_Store":
                rel_path = item.relative_to(template_path)
                content = item.read_text()
                files_to_create[str(rel_path)] = content
    else:
        # 2. Fallback to hardcoded templates
        template_data = PROJECT_TEMPLATES.get(template)
        if not template_data or not isinstance(template_data.get("files"), dict):
            print_error(f"Unknown template or directory '{template}'")
            # List available templates
            console.print("\n[bold]Available internal templates:[/bold]")
            for internal_template_name in PROJECT_TEMPLATES.keys():
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
                final_content = content.replace("{project_name}", project_name)

                full_path.write_text(final_content)
                progress.advance(task)

        print_success(f"Created project at [bold]{project_path}[/bold]")

        next_steps = f"""[bold]cd[/bold] {project_name}
[bold]pip[/bold] install -e .
[bold]baselith[/bold] run"""
        print_panel(next_steps, title="Next steps", style="green")

        return 0

    except Exception as e:
        print_error(f"Error creating project: {e}")
        # Cleanup on failure
        if project_path.exists():
            shutil.rmtree(project_path)
        return 1


def register_parser(subparsers, formatter_class):
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
    init_parser.add_argument(
        "--template",
        choices=["minimal", "full", "chat-only", "rag-system", "baselith-core"],
        help="Select a starter template (minimal, full, rag-system, etc.)",
    )
    return init_parser


__all__ = ["run_init", "register_parser"]
