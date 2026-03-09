"""
Plugin creation logic.
"""

import json
from pathlib import Path
from typing import Any

import yaml
from core.cli.ui import console, print_error, print_success, print_step, print_info
from .const import PLUGIN_TEMPLATE


PLUGINS_CONFIG_PATH = Path("configs") / "plugins.yaml"


def _prompt(label: str, default: str = "") -> str:
    """Prompt the user for input with an optional default."""
    suffix = f" [{default}]" if default else ""
    console.print(f"  [bold cyan]{label}{suffix}:[/bold cyan] ", end="")
    value = input().strip()
    return value if value else default


def create_plugin(
    name: str, plugin_type: str = "agent", interactive: bool = False
) -> int:
    """
    Create a new plugin from template.

    Args:
        name: Plugin name (lowercase with hyphens)
        plugin_type: Type of plugin (agent, router, graph)
        interactive: Whether to run interactive wizard mode

    Returns:
        Exit code (0 for success)
    """
    if interactive:
        return _create_interactive()

    return _create_from_template(name, plugin_type)


def _create_interactive() -> int:
    """Run interactive plugin creation wizard."""
    console.print()
    console.print("[bold blue]🧙 Plugin Creation Wizard[/bold blue]")
    console.print("[dim]Answer the following questions to scaffold your plugin.[/dim]")
    console.print()

    name = _prompt("Plugin name (kebab-case)", "")
    if not name:
        print_error("Plugin name is required.")
        return 1

    # Sanitize
    name = name.lower().replace(" ", "-").replace("_", "-")

    type_choice = _prompt("Plugin type (agent/router/graph)", "agent")
    if type_choice not in ("agent", "router", "graph"):
        print_error(f"Invalid plugin type '{type_choice}'.")
        return 1

    description = _prompt("Description", f"A custom {type_choice} plugin for {name}")
    author = _prompt("Author", "Baselith User")
    tags_raw = _prompt("Tags (comma-separated)", type_choice)
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()]
    env_vars_raw = _prompt("Environment variables (comma-separated, or empty)", "")
    env_vars = [v.strip() for v in env_vars_raw.split(",") if v.strip()]

    register_config = _prompt("Register in plugins.yaml? (y/n)", "y")

    # Create the plugin
    result = _create_from_template(name, type_choice)
    if result != 0:
        return result

    # Override manifest with interactive data
    plugin_path = Path("plugins") / name
    manifest_path = plugin_path / "manifest.json"
    if manifest_path.exists():
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            manifest["description"] = description
            manifest["author"] = author
            manifest["tags"] = tags
            if env_vars:
                manifest["environment_variables"] = env_vars
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=4)
        except Exception:
            pass

    # Register in plugins.yaml
    if register_config.lower() == "y":
        config: dict[str, Any] = {}
        if PLUGINS_CONFIG_PATH.exists():
            try:
                with open(PLUGINS_CONFIG_PATH, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f) or {}
            except Exception:
                pass

        config[name] = {"enabled": True}
        try:
            PLUGINS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(PLUGINS_CONFIG_PATH, "w", encoding="utf-8") as f:
                yaml.dump(
                    config,
                    f,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                )
            print_info(f"Registered '{name}' in {PLUGINS_CONFIG_PATH}")
        except Exception:
            pass

    console.print()
    print_success("Plugin created successfully with your custom metadata!")
    return 0


def _create_from_template(name: str, plugin_type: str) -> int:
    """Create a plugin from a built-in template."""
    plugins_dir = Path("plugins")
    if not plugins_dir.exists():
        plugins_dir.mkdir(parents=True)

    plugin_path = plugins_dir / name

    if plugin_path.exists():
        print_error(f"Plugin '{name}' already exists")
        return 1

    template = PLUGIN_TEMPLATE.get(plugin_type)
    if not template:
        print_error(f"Unknown plugin type '{plugin_type}'")
        return 1

    # Generate class name from plugin name
    class_name = "".join(word.capitalize() for word in name.split("-"))

    print_step(f"Creating {plugin_type} plugin '{name}'...")

    try:
        plugin_path.mkdir(parents=True)

        with console.status("[bold green]Generating plugin files..."):
            for file_name, content in template.items():
                file_path = plugin_path / file_name
                final_content = content.format(name=name, class_name=class_name)
                file_path.write_text(final_content)

        print_success(f"Created plugin at [bold]{plugin_path}[/bold]")

        console.print()
        console.print("[bold]Files created:[/bold]")
        for file_name in template.keys():
            console.print(f"  [cyan]- {plugin_path / file_name}[/cyan]")

        return 0

    except Exception as e:
        print_error(f"Error creating plugin: {e}")
        return 1
