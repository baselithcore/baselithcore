"""
Plugin configuration management commands.

Provides CLI access to `configs/plugins.yaml` for viewing and editing
plugin configuration without manual file editing.
"""

import json
from pathlib import Path
from typing import Optional

import yaml
from rich.panel import Panel
from rich.syntax import Syntax

from core.cli.ui import console, print_error, print_success, print_warning


PLUGINS_CONFIG_PATH = Path("configs") / "plugins.yaml"


def _load_config() -> dict:
    """Load plugins.yaml, returning empty dict if absent."""
    if not PLUGINS_CONFIG_PATH.exists():
        return {}
    try:
        with open(PLUGINS_CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        print_error(f"Failed to read {PLUGINS_CONFIG_PATH}: {e}")
        return {}


def _save_config(data: dict) -> bool:
    """Save config data back to plugins.yaml."""
    try:
        PLUGINS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(PLUGINS_CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(
                data, f, default_flow_style=False, allow_unicode=True, sort_keys=False
            )
        return True
    except Exception as e:
        print_error(f"Failed to write {PLUGINS_CONFIG_PATH}: {e}")
        return False


def _coerce_value(value: str):
    """Coerce string value to appropriate Python type."""
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def config_show(plugin_name: Optional[str] = None, json_output: bool = False) -> int:
    """
    Display plugin configuration from plugins.yaml.

    Args:
        plugin_name: Optional specific plugin to show. Shows all if None.
        json_output: Output as JSON instead of YAML syntax panel.

    Returns:
        Exit code.
    """
    config = _load_config()

    if not config:
        console.print("[yellow]No plugin configuration found.[/yellow]")
        return 0

    if plugin_name:
        if plugin_name not in config:
            print_error(f"Plugin '{plugin_name}' not found in configuration.")
            return 1
        config = {plugin_name: config[plugin_name]}

    if json_output:
        print(json.dumps(config, indent=2, default=str))
        return 0

    yaml_str = yaml.dump(
        config, default_flow_style=False, allow_unicode=True, sort_keys=False
    )
    syntax = Syntax(yaml_str, "yaml", theme="monokai", line_numbers=False)

    title = f"Plugin Config: {plugin_name}" if plugin_name else "Plugin Configuration"
    console.print()
    console.print(
        Panel(
            syntax,
            title=f"[bold]{title}[/bold]",
            subtitle=f"[dim]{PLUGINS_CONFIG_PATH}[/dim]",
            border_style="blue",
            padding=(1, 2),
        )
    )
    console.print()
    return 0


def config_set(plugin_name: str, key: str, value: str) -> int:
    """
    Set a configuration key for a plugin.

    Args:
        plugin_name: Plugin name in plugins.yaml.
        key: Configuration key to set.
        value: Value to assign (auto-coerced to bool/int/float).

    Returns:
        Exit code.
    """
    config = _load_config()

    if plugin_name not in config:
        config[plugin_name] = {}

    if not isinstance(config[plugin_name], dict):
        config[plugin_name] = {"enabled": config[plugin_name]}

    coerced = _coerce_value(value)
    config[plugin_name][key] = coerced

    if _save_config(config):
        print_success(f"Set [bold]{plugin_name}.{key}[/bold] = {coerced}")
        return 0
    return 1


def config_get(plugin_name: str, key: str, json_output: bool = False) -> int:
    """
    Get a specific configuration value.

    Args:
        plugin_name: Plugin name.
        key: Configuration key.
        json_output: Output as JSON.

    Returns:
        Exit code.
    """
    config = _load_config()

    if plugin_name not in config:
        print_error(f"Plugin '{plugin_name}' not found in configuration.")
        return 1

    plugin_config = config[plugin_name]
    if not isinstance(plugin_config, dict):
        print_error(f"Plugin '{plugin_name}' has no structured configuration.")
        return 1

    if key not in plugin_config:
        print_error(f"Key '{key}' not found in '{plugin_name}' configuration.")
        return 1

    value = plugin_config[key]
    if json_output:
        print(json.dumps({"plugin": plugin_name, "key": key, "value": value}))
    else:
        console.print(f"[bold cyan]{plugin_name}.{key}[/bold cyan] = {value}")

    return 0


def config_reset(plugin_name: str) -> int:
    """
    Reset a plugin's configuration to the default (enabled: false).

    Args:
        plugin_name: Plugin name to reset.

    Returns:
        Exit code.
    """
    config = _load_config()

    if plugin_name not in config:
        print_warning(
            f"Plugin '{plugin_name}' not found in configuration. Creating default entry."
        )

    config[plugin_name] = {"enabled": False}

    if _save_config(config):
        print_success(f"Reset configuration for '{plugin_name}' to defaults.")
        return 0
    return 1
