"""
Status and info commands for local plugins.
"""

import json
from pathlib import Path
from typing import Any, Optional

import yaml
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from .local_shared import PLUGINS_CONFIG_PATH, console
from core.cli.ui import print_error


def status_local_plugins(name: Optional[str] = None, json_output: bool = False) -> int:
    """
    Display comprehensive status for local plugins.
    """
    plugins_dir = Path("plugins")
    if not plugins_dir.exists():
        console.print("[yellow]No plugins directory found[/yellow]")
        return 0

    plugins = [
        p
        for p in plugins_dir.iterdir()
        if p.is_dir() and not p.name.startswith(".") and p.name != "__pycache__"
    ]

    if name:
        plugins = [p for p in plugins if p.name == name]
        if not plugins:
            print_error(f"Plugin '{name}' not found locally.")
            return 1

    if not plugins:
        if json_output:
            print(json.dumps({"status": "ok", "plugins": []}))
            return 0
        console.print("[yellow]No plugins installed[/yellow]")
        return 0

    json_result = []

    if not json_output:
        table = Table(
            title="Local Plugin Status" if not name else f"Status: {name}",
            title_style="bold blue",
            show_header=True,
            header_style="bold magenta",
            expand=True,
        )
        table.add_column("Status", style="dim", width=10, justify="center")
        table.add_column("Plugin Name", style="bold")
        table.add_column("Version", style="dim")
        table.add_column("Type")
        table.add_column("Readiness", width=10)
        table.add_column("Config", width=8, justify="center")
        table.add_column("Components")

    # Load plugins.yaml for config alignment
    yaml_config: dict[str, Any] = {}
    if PLUGINS_CONFIG_PATH.exists():
        try:
            with open(PLUGINS_CONFIG_PATH, "r", encoding="utf-8") as cf:
                yaml_config = yaml.safe_load(cf) or {}
        except Exception:
            pass

    for plugin in sorted(plugins):
        components = []
        is_healthy = False
        p_type = "Unknown"
        p_version = "Unknown"

        is_disabled = False

        if (plugin / "plugin.disabled").exists() or (
            plugin / "__init__.disabled"
        ).exists():
            is_disabled = True
            is_healthy = True
        elif (plugin / "__init__.py").exists():
            is_healthy = True

        plugin_file = plugin / "plugin.py"
        if not plugin_file.exists():
            plugin_file = plugin / "plugin.disabled"

        manifest_path = None
        for ext in [".yaml", ".yml", ".json"]:
            if (plugin / f"manifest{ext}").exists():
                manifest_path = plugin / f"manifest{ext}"
                break

        if manifest_path:
            try:
                if manifest_path.suffix in [".yaml", ".yml"]:
                    with open(manifest_path, "r", encoding="utf-8") as mf:
                        manifest_data = yaml.safe_load(mf) or {}
                else:
                    with open(manifest_path, "r", encoding="utf-8") as mf:
                        manifest_data = json.load(mf)

                tags = manifest_data.get("tags", [])
                p_version = manifest_data.get("version", "Unknown")
                p_type = "Generic"
                if "agent" in tags:
                    p_type = "Agent"
                elif "router" in tags:
                    p_type = "Router"
                elif "graph" in tags:
                    p_type = "Graph"

                # We can't know actual components from manifest without looking at python code,
                # but if AST fallback exists, we can still parse it.
                # However, for speed, we skip AST if manifest is found for `status`.
                # We put "Unknown" or infer from tags.
                components = [
                    c.capitalize() for c in tags if c in ["agent", "router", "graph"]
                ]
            except Exception:
                pass

        if not components and plugin_file.exists():
            content = plugin_file.read_text(encoding="utf-8")
            if "AgentPlugin" in content:
                p_type = "Agent"
            elif "RouterPlugin" in content or "APIRouter" in content:
                p_type = "Router"
            elif "GraphPlugin" in content:
                p_type = "Graph"
            else:
                p_type = "Generic"

            if "create_agent" in content:
                components.append("Agent")
            if "create_router" in content:
                components.append("Router")
            if "get_flow_handlers" in content:
                components.append("Flows")
            if "register_entity_types" in content:
                components.append("Graph Entities")
            if "register_relationship_types" in content:
                components.append("Graph Relationships")

        # Readiness badge
        readiness = "stable"
        if manifest_path:
            try:
                if manifest_path.suffix in [".yaml", ".yml"]:
                    with open(manifest_path, "r", encoding="utf-8") as mf:
                        m_data = yaml.safe_load(mf) or {}
                else:
                    with open(manifest_path, "r", encoding="utf-8") as mf:
                        m_data = json.load(mf)
                readiness = m_data.get("readiness", "stable")
            except Exception:
                pass

        # Config alignment
        in_config = plugin.name in yaml_config
        config_enabled = False
        if in_config:
            pc = yaml_config[plugin.name]
            config_enabled = (
                pc.get("enabled", False) if isinstance(pc, dict) else bool(pc)
            )

        if json_output:
            json_result.append(
                {
                    "name": plugin.name,
                    "version": p_version,
                    "type": p_type,
                    "readiness": readiness,
                    "components": components,
                    "in_config": in_config,
                    "config_enabled": config_enabled,
                    "status": "disabled"
                    if is_disabled
                    else ("active" if is_healthy else "broken"),
                }
            )
        else:
            if is_disabled:
                status_icon = "[dim]⏸️ Disabled[/dim]"
            else:
                status_icon = (
                    "[green]✅ Active[/green]" if is_healthy else "[red]❌ Broken[/red]"
                )

            # Readiness coloring
            readiness_color = (
                "green"
                if readiness == "stable"
                else ("yellow" if readiness == "beta" else "red")
            )
            readiness_str = f"[{readiness_color}]{readiness}[/{readiness_color}]"

            # Config alignment indicator
            if not in_config:
                config_str = "[dim]—[/dim]"
            elif config_enabled == (not is_disabled):
                config_str = "[green]✓[/green]"
            else:
                config_str = "[yellow]⚠[/yellow]"

            comp_str = ", ".join(components) if components else "None"
            table.add_row(
                status_icon,
                plugin.name,
                p_version,
                p_type,
                readiness_str,
                config_str,
                comp_str,
            )

    if json_output:
        print(json.dumps({"status": "ok", "plugins": json_result}))
    else:
        console.print(table)
        console.print()
        console.print(
            "[dim]Config column: ✓ = aligned  ⚠ = mismatch  — = not in plugins.yaml[/dim]"
        )
    return 0


def info_local_plugin(plugin_name: str, json_output: bool = False) -> int:
    """
    Get detailed information about a local plugin.
    """
    import ast

    plugin_dir = Path("plugins") / plugin_name

    if not plugin_dir.exists() or not plugin_dir.is_dir():
        print_error(f"Local plugin '{plugin_name}' not found.")
        return 1

    details = Text()
    details.append("Name: ", style="bold cyan")
    details.append(f"{plugin_name}\n")
    details.append("Path: ", style="bold cyan")
    details.append(f"{plugin_dir.absolute()}\n")

    details.append("Core Files: ", style="bold cyan")
    core_files = [
        "manifest.yaml",
        "manifest.json",
        "__init__.py",
        "plugin.py",
        "agent.py",
        "router.py",
        "README.md",
    ]
    for i, f in enumerate(core_files):
        if i > 0:
            details.append(", ")
        if (plugin_dir / f).exists():
            details.append(f, style="green")
        else:
            details.append(f, style="dim")
    details.append("\n\n")

    desc = "No description available."

    manifest_path = None
    for ext in [".yaml", ".yml", ".json"]:
        if (plugin_dir / f"manifest{ext}").exists():
            manifest_path = plugin_dir / f"manifest{ext}"
            break

    if manifest_path:
        try:
            if manifest_path.suffix in [".yaml", ".yml"]:
                with open(manifest_path, "r", encoding="utf-8") as mf:
                    manifest_data = yaml.safe_load(mf) or {}
            else:
                with open(manifest_path, "r", encoding="utf-8") as mf:
                    manifest_data = json.load(mf)
            desc = manifest_data.get("description", desc)
        except Exception:
            pass
    else:
        plugin_file = plugin_dir / "plugin.py"
        if plugin_file.exists():
            content = plugin_file.read_text(encoding="utf-8")
            try:
                tree = ast.parse(content)
                doc = ast.get_docstring(tree)
                if doc:
                    desc = doc.strip()
            except Exception:
                pass

    details.append("Description:\n", style="bold cyan")
    details.append(f"{desc}\n\n")

    if manifest_path:
        try:
            if manifest_path.suffix in [".yaml", ".yml"]:
                with open(manifest_path, "r", encoding="utf-8") as mf:
                    data = yaml.safe_load(mf) or {}
            else:
                with open(manifest_path, "r", encoding="utf-8") as mf:
                    data = json.load(mf)
            category = data.get("category", "Generic")
            readiness = data.get("readiness", "stable")
            version = data.get("version", "Unknown")

            details.append("Version: ", style="bold cyan")
            details.append(f"{version}\n")
            details.append("Category: ", style="bold cyan")
            details.append(f"{category}\n")
            details.append("Readiness: ", style="bold cyan")

            color = (
                "green"
                if readiness == "stable"
                else ("yellow" if readiness == "beta" else "red")
            )
            details.append(f"{readiness}", style=f"bold {color}")
            details.append("\n")

            envs = data.get("environment_variables", [])
            if envs:
                details.append("Required Envs: ", style="bold cyan")
                details.append(f"{', '.join(envs)}\n")
        except Exception:
            pass

    if json_output:
        print(
            json.dumps(
                {
                    "name": plugin_name,
                    "path": str(plugin_dir.absolute()),
                    "files": [
                        f
                        for f in [
                            "manifest.yaml",
                            "manifest.json",
                            "__init__.py",
                            "plugin.py",
                            "agent.py",
                            "router.py",
                            "README.md",
                        ]
                        if (plugin_dir / f).exists()
                    ],
                    "description": desc,
                }
            )
        )
        return 0

    panel = Panel(
        details,
        title=f"[bold]Local Plugin: {plugin_name}[/bold]",
        border_style="blue",
        padding=(1, 2),
    )
    console.print()
    console.print(panel)
    console.print()
    return 0
