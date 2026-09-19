"""
Plugin dependency management commands.

Provides `deps check` and `deps install` subcommands for verifying
and resolving plugin dependencies (Python packages, sibling plugins,
environment variables, and required resources).
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TypedDict

import yaml
from packaging.requirements import InvalidRequirement, Requirement
from rich.table import Table

from core.cli.commands.doctor_checks import env_value, is_placeholder_secret
from core.cli.ui import console, print_error, print_step, print_success, print_warning


def _is_valid_requirement(spec: object) -> bool:
    """True only for a plain PEP 508 requirement string.

    A manifest ``python_dependencies`` entry is fed to ``pip install``; anything
    that is not a bare requirement — a pip option like ``--index-url=…`` or
    ``--find-links=…``, or a bare flag — would be interpreted by pip as an
    option (dependency-confusion vector), so those are rejected.
    """
    if not isinstance(spec, str) or spec.strip().startswith("-"):
        return False
    try:
        Requirement(spec)
    except InvalidRequirement:
        return False
    return True


def _load_manifest(plugin_dir: Path) -> dict | None:
    """Load manifest data from a plugin directory."""
    for ext in [".yaml", ".yml", ".json"]:
        manifest_path = plugin_dir / f"manifest{ext}"
        if manifest_path.exists():
            try:
                with open(manifest_path, encoding="utf-8") as f:
                    manifest: dict | None
                    if ext in (".yaml", ".yml"):
                        manifest = yaml.safe_load(f) or {}
                    else:
                        manifest = json.load(f)
                    return manifest
            except Exception:
                return None
    return None


def _check_python_dep(package: str) -> bool:
    """Check if a Python package is importable."""
    from importlib.metadata import PackageNotFoundError as PNF
    from importlib.metadata import distribution

    try:
        package = Requirement(package).name
    except InvalidRequirement:
        return False

    try:
        distribution(package)
        return True
    except PNF:
        return False


def _local_plugin_names() -> list[str]:
    """Return visible local plugin names."""
    plugins_dir = Path("plugins")
    if not plugins_dir.exists():
        return []
    return [
        path.name
        for path in sorted(plugins_dir.iterdir())
        if path.is_dir()
        and not path.name.startswith(".")
        and path.name != "__pycache__"
    ]


def _check_plugin_dep(plugin_name: str) -> bool:
    """Check if a sibling plugin exists locally."""
    return (Path("plugins") / plugin_name).is_dir()


def _check_env_var(var: str) -> bool:
    """Check if an environment variable is set."""
    return env_value(var) is not None


def _check_resource(resource: object) -> bool:
    """Check known logical resources declared by plugin manifests."""
    if not isinstance(resource, str):
        return False
    normalized = resource.lower()
    if normalized == "llm":
        return not is_placeholder_secret(env_value("LLM_API_KEY"))
    if normalized == "postgres":
        return (env_value("POSTGRES_ENABLED", "true") or "true").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    if normalized == "redis":
        return (
            env_value("CACHE_REDIS_URL") is not None
            or env_value("REDIS_URL") is not None
        )
    if normalized == "qdrant":
        return (
            env_value("VECTORSTORE_PROVIDER", "qdrant") or "qdrant"
        ).lower() == "qdrant"
    if normalized == "sandbox":
        return env_value("SANDBOX_ENABLED") is not None or os.path.exists(
            env_value("SANDBOX_DOCKER_SOCKET", "/var/run/docker.sock")
            or "/var/run/docker.sock"
        )
    return _check_env_var(resource)


def deps_check(
    plugin_name: str, json_output: bool = False, python_only: bool = False
) -> int:
    """
    Check all declared dependencies for a plugin.

    Verifies python_dependencies, plugin_dependencies,
    environment_variables, and required_resources from the manifest.

    Args:
        plugin_name: Name of the plugin to check.
        json_output: Whether to output JSON instead of Rich tables.
        python_only: Only check Python package dependencies.

    Returns:
        Exit code (0 = all satisfied, 1 = issues found).
    """
    plugin_dir = Path("plugins") / plugin_name

    if not plugin_dir.exists() or not plugin_dir.is_dir():
        print_error(f"Plugin '{plugin_name}' not found.")
        return 1

    from core.plugins.env import load_plugin_dotenv

    load_plugin_dotenv(plugin_dir)

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

    if not python_only:
        plugin_dep_names = (
            plugin_deps.keys() if isinstance(plugin_deps, dict) else plugin_deps
        )
        for dep in plugin_dep_names:
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

        for res in required_res:
            satisfied = _check_resource(res)
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


class _DependencySummary(TypedDict):
    plugin: str
    all_satisfied: bool
    missing: list[str]


def deps_check_all(json_output: bool = False, python_only: bool = False) -> int:
    """Check dependencies for all local plugins."""
    plugin_names = _local_plugin_names()
    if not plugin_names:
        if json_output:
            print(json.dumps({"plugins": [], "all_satisfied": True}))
        else:
            print_warning("No local plugins found.")
        return 0

    results: list[_DependencySummary] = []
    all_ok = True
    for plugin_name in plugin_names:
        plugin_dir = Path("plugins") / plugin_name
        manifest = _load_manifest(plugin_dir)
        if manifest is None:
            continue
        python_deps = manifest.get("python_dependencies", [])
        plugin_deps = manifest.get("plugin_dependencies", [])
        env_vars = manifest.get("environment_variables", [])
        required_res = manifest.get("required_resources", [])
        missing = []
        for dep in python_deps:
            if not _check_python_dep(dep):
                missing.append(str(dep))
        if not python_only:
            plugin_dep_names = (
                plugin_deps.keys() if isinstance(plugin_deps, dict) else plugin_deps
            )
            for dep in plugin_dep_names:
                if not _check_plugin_dep(dep):
                    missing.append(str(dep))
            for var in env_vars:
                if not _check_env_var(var):
                    missing.append(str(var))
            for res in required_res:
                if not _check_resource(res):
                    missing.append(str(res))
        if missing:
            all_ok = False
        results.append(
            {
                "plugin": plugin_name,
                "all_satisfied": not missing,
                "missing": missing,
            }
        )

    if json_output:
        print(json.dumps({"plugins": results, "all_satisfied": all_ok}))
        return 0 if all_ok else 1

    table = Table(
        title="Plugin Dependencies",
        title_style="bold blue",
        show_header=True,
        header_style="bold magenta",
        expand=True,
    )
    table.add_column("Plugin", style="cyan")
    table.add_column("Status", justify="center", width=12)
    table.add_column("Missing", style="dim")
    for result in results:
        missing = result["missing"]
        status = "[green]✅ OK[/green]" if not missing else "[red]❌ Missing[/red]"
        table.add_row(result["plugin"], status, ", ".join(missing))
    console.print(table)
    return 0 if all_ok else 1


def _missing_python_deps(plugin_name: str) -> tuple[list[str], list[str] | None]:
    """Return missing Python deps and an optional error list for one plugin."""
    plugin_dir = Path("plugins") / plugin_name
    if not plugin_dir.exists() or not plugin_dir.is_dir():
        return [], [f"Plugin '{plugin_name}' not found."]

    manifest = _load_manifest(plugin_dir)
    if manifest is None:
        return [], [f"No manifest found for plugin '{plugin_name}'."]

    python_deps = manifest.get("python_dependencies", [])
    invalid = [dep for dep in python_deps if not _is_valid_requirement(dep)]
    if invalid:
        return [], [
            "Manifest python_dependencies contains entries that are not valid "
            f"PEP 508 requirements: {', '.join(map(str, invalid))}"
        ]

    return [dep for dep in python_deps if not _check_python_dep(dep)], None


def deps_install(plugin_name: str, yes: bool = False, dry_run: bool = False) -> int:
    """
    Install missing Python dependencies for a plugin.

    Reads `python_dependencies` from the manifest and attempts
    to pip-install any that are not already available.

    Args:
        plugin_name: Name of the plugin.
        yes: Skip confirmation prompt.
        dry_run: Print missing packages without installing them.

    Returns:
        Exit code (0 for success).
    """
    missing, errors = _missing_python_deps(plugin_name)
    if errors:
        print_error(errors[0])
        return 1

    if not missing:
        print_success("All Python dependencies are already installed.")
        return 0

    if dry_run:
        console.print(
            f"[bold]{plugin_name} missing packages:[/bold] {', '.join(missing)}"
        )
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
            # `--` terminates option parsing so every remaining arg is treated
            # as a positional requirement, not a pip flag.
            [sys.executable, "-m", "pip", "install", "--", *missing],
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


def deps_install_all(yes: bool = False, dry_run: bool = False) -> int:
    """Install missing Python dependencies for all local plugins."""
    plugin_names = _local_plugin_names()
    if not plugin_names:
        print_warning("No local plugins found.")
        return 0

    failed: list[str] = []
    pending: dict[str, list[str]] = {}
    for plugin_name in plugin_names:
        plugin_dir = Path("plugins") / plugin_name
        manifest = _load_manifest(plugin_dir)
        if manifest is None:
            continue
        missing, errors = _missing_python_deps(plugin_name)
        if errors:
            failed.append(plugin_name)
            continue
        if missing:
            pending[plugin_name] = missing

    if dry_run:
        if not pending and not failed:
            print_success("All plugin Python dependencies are already installed.")
            return 0
        table = Table(
            title="Missing Plugin Python Dependencies",
            title_style="bold blue",
            show_header=True,
            header_style="bold magenta",
            expand=True,
        )
        table.add_column("Plugin", style="cyan")
        table.add_column("Packages", style="dim")
        for plugin_name, missing in pending.items():
            table.add_row(plugin_name, ", ".join(missing))
        for plugin_name in failed:
            table.add_row(plugin_name, "[red]Invalid or missing manifest[/red]")
        console.print(table)
        return 1 if failed else 0

    for plugin_name in pending:
        result = deps_install(plugin_name, yes=yes)
        if result != 0 and plugin_name not in failed:
            failed.append(plugin_name)

    if failed:
        print_error(
            "Some plugin dependencies could not be installed",
            ", ".join(failed),
        )
        return 1

    print_success("All plugin Python dependencies are installed.")
    return 0
