"""
Validation logic for local plugins.
"""

import ast
import json
import os
from pathlib import Path

import yaml
from rich.table import Table

from .local_shared import console
from core.cli.ui import print_error, print_success, print_warning


def validate_local_plugin(plugin_name: str, json_output: bool = False) -> int:
    """
    Validate the syntax, structure, manifest, and dependencies of a local plugin.

    Performs comprehensive validation:
    - Python AST syntax check
    - Plugin base class detection
    - Manifest schema validation (name, version, description)
    - Environment variable presence check
    - Python dependency importability check
    - Plugin dependency existence check
    """
    plugin_dir = Path("plugins") / plugin_name

    if not plugin_dir.exists() or not plugin_dir.is_dir():
        print_error(f"Local plugin '{plugin_name}' not found.")
        return 1

    plugin_file = plugin_dir / "plugin.py"
    if not plugin_file.exists():
        plugin_file = plugin_dir / "plugin.disabled"

    if not plugin_file.exists():
        print_error(f"No 'plugin.py' or 'plugin.disabled' found in '{plugin_name}'.")
        return 1

    console.print(f"\n[bold cyan]Validating plugin '{plugin_name}'...[/bold cyan]\n")

    checks: list[dict] = []
    all_passed = True

    # 1. Python syntax check
    try:
        content = plugin_file.read_text(encoding="utf-8")
        tree = ast.parse(content)
        checks.append({"check": "Python Syntax", "passed": True, "detail": "No errors"})
    except SyntaxError as e:
        checks.append({"check": "Python Syntax", "passed": False, "detail": str(e)})
        all_passed = False
        tree = None
    except Exception as e:
        checks.append({"check": "Python Syntax", "passed": False, "detail": str(e)})
        all_passed = False
        tree = None

    # 2. Plugin class detection
    valid_bases = {"Plugin", "AgentPlugin", "RouterPlugin", "GraphPlugin"}
    plugin_classes = []

    if tree:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = []
                for base in node.bases:
                    if isinstance(base, ast.Name):
                        bases.append(base.id)
                    elif isinstance(base, ast.Attribute):
                        bases.append(base.attr)
                if any(b in valid_bases for b in bases):
                    plugin_classes.append(node.name)

    if plugin_classes:
        checks.append(
            {
                "check": "Plugin Class",
                "passed": True,
                "detail": ", ".join(plugin_classes),
            }
        )
    else:
        checks.append(
            {
                "check": "Plugin Class",
                "passed": False,
                "detail": f"No class extending {', '.join(valid_bases)}",
            }
        )
        all_passed = False

    # 3. Manifest schema validation
    manifest_data = None
    for ext in [".yaml", ".yml", ".json"]:
        mpath = plugin_dir / f"manifest{ext}"
        if mpath.exists():
            try:
                with open(mpath, "r", encoding="utf-8") as mf:
                    if ext in (".yaml", ".yml"):
                        manifest_data = yaml.safe_load(mf) or {}
                    else:
                        manifest_data = json.load(mf)
            except Exception as e:
                checks.append(
                    {"check": "Manifest Parse", "passed": False, "detail": str(e)}
                )
                all_passed = False
            break

    if manifest_data is not None:
        required_fields = ["name", "version", "description"]
        missing_fields = [f for f in required_fields if f not in manifest_data]
        if missing_fields:
            checks.append(
                {
                    "check": "Manifest Schema",
                    "passed": False,
                    "detail": f"Missing: {', '.join(missing_fields)}",
                }
            )
            all_passed = False
        else:
            checks.append(
                {
                    "check": "Manifest Schema",
                    "passed": True,
                    "detail": f"v{manifest_data.get('version', '?')}",
                }
            )

        # 4. Environment variables check
        env_vars = manifest_data.get("environment_variables", [])
        if env_vars:
            missing_env = [v for v in env_vars if v not in os.environ]
            if missing_env:
                checks.append(
                    {
                        "check": "Env Variables",
                        "passed": False,
                        "detail": f"Missing: {', '.join(missing_env)}",
                    }
                )
                all_passed = False
            else:
                checks.append(
                    {
                        "check": "Env Variables",
                        "passed": True,
                        "detail": f"{len(env_vars)} defined",
                    }
                )

        # 5. Python dependency check
        python_deps = manifest_data.get("python_dependencies", [])
        if python_deps:
            from importlib.metadata import distribution, PackageNotFoundError as PNF

            missing_pkgs = []
            for dep in python_deps:
                try:
                    distribution(dep)
                except PNF:
                    missing_pkgs.append(dep)
            if missing_pkgs:
                checks.append(
                    {
                        "check": "Python Deps",
                        "passed": False,
                        "detail": f"Missing: {', '.join(missing_pkgs)}",
                    }
                )
                all_passed = False
            else:
                checks.append(
                    {
                        "check": "Python Deps",
                        "passed": True,
                        "detail": f"{len(python_deps)} satisfied",
                    }
                )

        # 6. Plugin dependency check
        plugin_deps = manifest_data.get("plugin_dependencies", [])
        if plugin_deps:
            missing_plugins = [
                d for d in plugin_deps if not (Path("plugins") / d).is_dir()
            ]
            if missing_plugins:
                checks.append(
                    {
                        "check": "Plugin Deps",
                        "passed": False,
                        "detail": f"Missing: {', '.join(missing_plugins)}",
                    }
                )
                all_passed = False
            else:
                checks.append(
                    {
                        "check": "Plugin Deps",
                        "passed": True,
                        "detail": f"{len(plugin_deps)} satisfied",
                    }
                )
    else:
        checks.append(
            {"check": "Manifest", "passed": False, "detail": "No manifest file found"}
        )
        all_passed = False

    # Output
    if json_output:
        print(
            json.dumps({"plugin": plugin_name, "valid": all_passed, "checks": checks})
        )
        return 0 if all_passed else 1

    table = Table(
        title=f"Validation Report: {plugin_name}",
        title_style="bold blue",
        show_header=True,
        header_style="bold magenta",
        expand=True,
    )
    table.add_column("Check", style="cyan", width=18)
    table.add_column("Status", justify="center", width=10)
    table.add_column("Detail")

    for c in checks:
        icon = "[green]✅ Pass[/green]" if c["passed"] else "[red]❌ Fail[/red]"
        table.add_row(c["check"], icon, c["detail"])

    console.print(table)
    console.print()

    if all_passed:
        print_success(f"Plugin '{plugin_name}' passed all validation checks.")
    else:
        failed = sum(1 for c in checks if not c["passed"])
        print_warning(f"{failed} check(s) failed for plugin '{plugin_name}'.")

    return 0 if all_passed else 1
