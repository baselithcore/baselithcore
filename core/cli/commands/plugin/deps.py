"""
Plugin dependency management commands.

Provides `deps check` and `deps install` subcommands for verifying
and resolving plugin dependencies (Python packages, sibling plugins,
environment variables, and required resources).
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import json
import yaml
from rich.table import Table

from core.cli.ui import console, print_error, print_success, print_step, print_warning


def _load_manifest(plugin_dir: Path) -> Optional[dict]:
    """Load manifest data from a plugin directory."""
    for ext in [".yaml", ".yml", ".json"]:
        manifest_path = plugin_dir / f"manifest{ext}"
        if manifest_path.exists():
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    if ext in (".yaml", ".yml"):
                        return yaml.safe_load(f) or {}
                    return json.load(f)
            except Exception:
                return None
    return None


def _check_python_dep(package: str) -> bool:
    """Check if a Python package is importable."""
    from importlib.metadata import distribution, PackageNotFoundError as PNF

    try:
        distribution(package)
        return True
    except PNF:
        return False


def _check_plugin_dep(plugin_name: str) -> bool:
    """Check if a sibling plugin exists locally."""
    return (Path("plugins") / plugin_name).is_dir()


def _check_env_var(var: str) -> bool:
    """Check if an environment variable is set."""
    return var in os.environ


def deps_check(plugin_name: str, json_output: bool = False) -> int:
    """
    Check all declared dependencies for a plugin.

    Verifies python_dependencies, plugin_dependencies,
    environment_variables, and required_resources from the manifest.

    Args:
        plugin_name: Name of the plugin to check.
        json_output: Whether to output JSON instead of Rich tables.

    Returns:
        Exit code (0 = all satisfied, 1 = issues found).
    """
    plugin_dir = Path("plugins") / plugin_name

    if not plugin_dir.exists() or not plugin_dir.is_dir():
        print_error(f"Plugin '{plugin_name}' not found.")
        return 1

    manifest = _load_manifest(plugin_dir)
    if manifest is None:
        print_error(f"No manifest found for plugin '{plugin_name}'.")
        return 1

    python_deps = manifest.get("python_dependencies", [])
    plugin_deps = manifest.get("plugin_dependencies", [])
    env_vars = manifest.get("environment_variables", [])
    required_res = manifest.get("required_resources", [])

    all_ok = True
    results: list[dict] = []

    # Python dependencies
    for dep in python_deps:
        satisfied = _check_python_dep(dep)
        if not satisfied:
            all_ok = False
        results.append(
            {
                "category": "Python Package",
                "name": dep,
                "status": "satisfied" if satisfied else "missing",
            }
        )

    # Plugin dependencies
    for dep in plugin_deps:
        satisfied = _check_plugin_dep(dep)
        if not satisfied:
            all_ok = False
        results.append(
            {
                "category": "Plugin",
                "name": dep,
                "status": "satisfied" if satisfied else "missing",
            }
        )

    # Environment variables
    for var in env_vars:
        satisfied = _check_env_var(var)
        if not satisfied:
            all_ok = False
        results.append(
            {
                "category": "Environment Var",
                "name": var,
                "status": "satisfied" if satisfied else "missing",
            }
        )

    # Required resources
    for res in required_res:
        # Resources are advisory; we just check environment hints
        satisfied = _check_env_var(res) if isinstance(res, str) else False
        if not satisfied:
            all_ok = False
        results.append(
            {
                "category": "Resource",
                "name": res if isinstance(res, str) else str(res),
                "status": "satisfied" if satisfied else "missing",
            }
        )

    if json_output:
        print(
            json.dumps(
                {
                    "plugin": plugin_name,
                    "all_satisfied": all_ok,
                    "dependencies": results,
                }
            )
        )
        return 0 if all_ok else 1

    # No dependencies declared
    if not results:
        console.print(f"[dim]Plugin '{plugin_name}' declares no dependencies.[/dim]")
        return 0

    table = Table(
        title=f"Dependencies: {plugin_name}",
        title_style="bold blue",
        show_header=True,
        header_style="bold magenta",
        expand=True,
    )
    table.add_column("Category", style="cyan", width=18)
    table.add_column("Dependency", style="bold")
    table.add_column("Status", justify="center", width=12)

    for r in results:
        icon = (
            "[green]✅ OK[/green]"
            if r["status"] == "satisfied"
            else "[red]❌ Missing[/red]"
        )
        table.add_row(r["category"], r["name"], icon)

    console.print()
    console.print(table)
    console.print()

    if all_ok:
        print_success("All dependencies satisfied.")
    else:
        missing = [r["name"] for r in results if r["status"] == "missing"]
        print_warning(
            f"{len(missing)} unsatisfied dependency(ies): {', '.join(missing)}"
        )

    return 0 if all_ok else 1


def deps_install(plugin_name: str, yes: bool = False) -> int:
    """
    Install missing Python dependencies for a plugin.

    Reads `python_dependencies` from the manifest and attempts
    to pip-install any that are not already available.

    Args:
        plugin_name: Name of the plugin.
        yes: Skip confirmation prompt.

    Returns:
        Exit code (0 for success).
    """
    plugin_dir = Path("plugins") / plugin_name

    if not plugin_dir.exists() or not plugin_dir.is_dir():
        print_error(f"Plugin '{plugin_name}' not found.")
        return 1

    manifest = _load_manifest(plugin_dir)
    if manifest is None:
        print_error(f"No manifest found for plugin '{plugin_name}'.")
        return 1

    python_deps = manifest.get("python_dependencies", [])
    if not python_deps:
        console.print(f"[dim]Plugin '{plugin_name}' has no Python dependencies.[/dim]")
        return 0

    missing = [dep for dep in python_deps if not _check_python_dep(dep)]
    if not missing:
        print_success("All Python dependencies are already installed.")
        return 0

    console.print(f"\n[bold]Missing packages:[/bold] {', '.join(missing)}\n")

    if not yes:
        console.print("[yellow]Install these packages?[/yellow] (y/N)")
        response = input().strip().lower()
        if response != "y":
            console.print("Operation cancelled.")
            return 0

    print_step(f"Installing {len(missing)} package(s)...")

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", *missing],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            print_success(f"Successfully installed: {', '.join(missing)}")
            return 0
        else:
            print_error("pip install failed", result.stderr.strip())
            return 1
    except Exception as e:
        print_error(f"Failed to run pip: {e}")
        return 1
