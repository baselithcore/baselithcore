"""
Info command - Display system and project dashboard.
"""

import json as json_lib
import platform
from pathlib import Path
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich import box
from importlib.metadata import version, PackageNotFoundError

from core.cli.ui import console, print_header, Timer, print_timing


def run_info(json_output: bool = False) -> int:
    """
    Display a dashboard with system and project information.

    Args:
        json_output: If True, emit machine-readable JSON.
    """
    timer = Timer()

    with timer:
        try:
            core_version = version("baselith-core")
        except PackageNotFoundError:
            core_version = "0.0.0-dev"

        # Identify project
        is_project = False
        project_name = "N/A"
        pyproject_path = Path.cwd() / "pyproject.toml"

        if pyproject_path.exists():
            is_project = True
            try:
                for line in pyproject_path.read_text().splitlines():
                    if line.startswith("name = "):
                        project_name = line.split("=")[1].strip().strip('"').strip("'")
                        break
            except Exception:
                project_name = "Unknown"

        # Count plugins
        plugins_dir = Path.cwd() / "plugins"
        plugin_count = (
            len(
                [
                    p
                    for p in plugins_dir.iterdir()
                    if p.is_dir() and not p.name.startswith(".")
                ]
            )
            if plugins_dir.exists()
            else 0
        )

    # ── JSON output ──────────────────────────
    if json_output:
        output = {
            "framework": {
                "version": core_version,
                "python": platform.python_version(),
                "os": f"{platform.system()} {platform.release()}",
            },
            "project": {
                "name": project_name,
                "detected": is_project,
                "plugin_count": plugin_count,
                "path": str(Path.cwd()),
            },
            "elapsed_seconds": round(timer.elapsed, 3),
        }
        console.print_json(json_lib.dumps(output))
        return 0

    # ── Rich layout output ───────────────────
    print_header("✨ Baselith-Core Dashboard ✨")

    # Layout construction
    layout = Layout()
    layout.split_row(Layout(name="framework"), Layout(name="project"))

    # Framework Panel
    fw_table = Table(box=box.SIMPLE, show_header=False)
    fw_table.add_column("Key", style="dim")
    fw_table.add_column("Value", style="bold cyan")
    fw_table.add_row("Version", core_version)
    fw_table.add_row("Python", platform.python_version())
    fw_table.add_row("OS", f"{platform.system()} {platform.release()}")

    fw_panel = Panel(fw_table, title="[bold blue]Framework", border_style="blue")
    layout["framework"].update(fw_panel)

    # Project Panel
    proj_table = Table(box=box.SIMPLE, show_header=False)
    proj_table.add_column("Key", style="dim")
    proj_table.add_column("Value", style="bold magenta")
    proj_table.add_row("Name", project_name)
    proj_table.add_row("In Project", "✅ Yes" if is_project else "❌ No")
    proj_table.add_row("Plugins", str(plugin_count))
    proj_table.add_row("Path", str(Path.cwd()))

    proj_panel = Panel(
        proj_table, title="[bold magenta]Current Workspace", border_style="magenta"
    )
    layout["project"].update(proj_panel)

    # Render
    console.print(layout)
    print_timing(timer.elapsed)

    return 0


def register_parser(subparsers, formatter_class):
    """Register 'info' command parser."""
    info_parser = subparsers.add_parser(
        "info",
        help="View system dashboard",
        description="View project metadata, active plugins, and framework telemetry in a beautiful Rich layout.",
        formatter_class=formatter_class,
    )
    info_parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Emit machine-readable JSON output for CI/CD pipelines",
    )


__all__ = ["run_info", "register_parser"]
