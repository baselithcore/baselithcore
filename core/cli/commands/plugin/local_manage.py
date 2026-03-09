"""
Management commands for local plugins (enable, disable, delete).
"""

import shutil
from pathlib import Path
from typing import Any

import yaml
from .local_shared import PLUGINS_CONFIG_PATH, console
from core.cli.ui import print_error, print_success


def _sync_config_enabled(plugin_name: str, enabled: bool) -> None:
    """Sync a plugin's enabled state in configs/plugins.yaml."""
    config: dict[str, Any] = {}
    if PLUGINS_CONFIG_PATH.exists():
        try:
            with open(PLUGINS_CONFIG_PATH, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
        except Exception:
            return

    if plugin_name not in config:
        config[plugin_name] = {}
    if isinstance(config[plugin_name], dict):
        config[plugin_name]["enabled"] = enabled
    else:
        config[plugin_name] = {"enabled": enabled}

    try:
        PLUGINS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(PLUGINS_CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(
                config, f, default_flow_style=False, allow_unicode=True, sort_keys=False
            )
    except Exception:
        pass


def delete_local_plugin(plugin_name: str, force: bool = False) -> int:
    """
    Delete a local plugin directory.
    """
    plugin_dir = Path("plugins") / plugin_name

    if not plugin_dir.exists() or not plugin_dir.is_dir():
        print_error(f"Local plugin '{plugin_name}' not found.")
        return 1

    if not force:
        console.print(
            f"[yellow]Are you sure you want to delete the plugin '{plugin_name}'?[/yellow] (y/N)"
        )
        response = input().strip().lower()
        if response != "y":
            console.print("Operation cancelled.")
            return 0

    try:
        shutil.rmtree(plugin_dir)
        print_success(f"Plugin '{plugin_name}' successfully deleted.")
        return 0
    except Exception as e:
        print_error(f"Error deleting plugin '{plugin_name}': {e}")
        return 1


def disable_local_plugin(plugin_name: str, all_plugins: bool = False) -> int:
    """
    Disable a local plugin by renaming its entry files.

    Args:
        plugin_name: Name of the plugin (ignored if all_plugins is True).
        all_plugins: Disable all installed plugins.
    """
    if all_plugins:
        return _bulk_toggle(enable=False)

    plugin_dir = Path("plugins") / plugin_name

    if not plugin_dir.exists() or not plugin_dir.is_dir():
        print_error(f"Local plugin '{plugin_name}' not found.")
        return 1

    plugin_file = plugin_dir / "plugin.py"
    init_file = plugin_dir / "__init__.py"

    if not plugin_file.exists() and not init_file.exists():
        if (plugin_dir / "plugin.disabled").exists() or (
            plugin_dir / "__init__.disabled"
        ).exists():
            console.print(
                f"[yellow]Plugin '{plugin_name}' is already disabled.[/yellow]"
            )
            return 0
        else:
            print_error(f"No valid entry files found for plugin '{plugin_name}'.")
            return 1

    try:
        if plugin_file.exists():
            plugin_file.rename(plugin_dir / "plugin.disabled")
        if init_file.exists():
            init_file.rename(plugin_dir / "__init__.disabled")
        _sync_config_enabled(plugin_name, False)
        print_success(f"Plugin '{plugin_name}' disabled successfully.")
        return 0
    except Exception as e:
        print_error(f"Error disabling plugin '{plugin_name}': {e}")
        return 1


def enable_local_plugin(plugin_name: str, all_plugins: bool = False) -> int:
    """
    Enable a previously disabled local plugin.

    Args:
        plugin_name: Name of the plugin (ignored if all_plugins is True).
        all_plugins: Enable all disabled plugins.
    """
    if all_plugins:
        return _bulk_toggle(enable=True)

    plugin_dir = Path("plugins") / plugin_name

    if not plugin_dir.exists() or not plugin_dir.is_dir():
        print_error(f"Local plugin '{plugin_name}' not found.")
        return 1

    disabled_plugin = plugin_dir / "plugin.disabled"
    disabled_init = plugin_dir / "__init__.disabled"

    if not disabled_plugin.exists() and not disabled_init.exists():
        if (plugin_dir / "plugin.py").exists() or (plugin_dir / "__init__.py").exists():
            console.print(
                f"[yellow]Plugin '{plugin_name}' is already enabled.[/yellow]"
            )
            return 0
        else:
            print_error(f"No disabled entry files found for plugin '{plugin_name}'.")
            return 1

    try:
        if disabled_plugin.exists():
            disabled_plugin.rename(plugin_dir / "plugin.py")
        if disabled_init.exists():
            disabled_init.rename(plugin_dir / "__init__.py")
        _sync_config_enabled(plugin_name, True)
        print_success(f"Plugin '{plugin_name}' enabled successfully.")
        return 0
    except Exception as e:
        print_error(f"Error enabling plugin '{plugin_name}': {e}")
        return 1


def _bulk_toggle(enable: bool) -> int:
    """Enable or disable all installed plugins."""
    plugins_dir = Path("plugins")
    if not plugins_dir.exists():
        print_error("No plugins directory found.")
        return 1

    count = 0

    for plugin_dir in sorted(plugins_dir.iterdir()):
        if (
            not plugin_dir.is_dir()
            or plugin_dir.name.startswith(".")
            or plugin_dir.name == "__pycache__"
        ):
            continue

        if enable:
            disabled_plugin = plugin_dir / "plugin.disabled"
            disabled_init = plugin_dir / "__init__.disabled"
            if disabled_plugin.exists() or disabled_init.exists():
                try:
                    if disabled_plugin.exists():
                        disabled_plugin.rename(plugin_dir / "plugin.py")
                    if disabled_init.exists():
                        disabled_init.rename(plugin_dir / "__init__.py")
                    _sync_config_enabled(plugin_dir.name, True)
                    console.print(f"  [green]✅[/green] {plugin_dir.name}")
                    count += 1
                except Exception as e:
                    console.print(f"  [red]❌[/red] {plugin_dir.name}: {e}")
        else:
            plugin_file = plugin_dir / "plugin.py"
            init_file = plugin_dir / "__init__.py"
            if plugin_file.exists() or init_file.exists():
                try:
                    if plugin_file.exists():
                        plugin_file.rename(plugin_dir / "plugin.disabled")
                    if init_file.exists():
                        init_file.rename(plugin_dir / "__init__.disabled")
                    _sync_config_enabled(plugin_dir.name, False)
                    console.print(f"  [dim]⏸️[/dim]  {plugin_dir.name}")
                    count += 1
                except Exception as e:
                    console.print(f"  [red]❌[/red] {plugin_dir.name}: {e}")

    verb = "enabled" if enable else "disabled"
    print_success(f"{count} plugin(s) {verb}.")
    return 0
