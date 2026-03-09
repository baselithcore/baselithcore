"""
Export manifest command for plugins.
"""

from pathlib import Path
import sys
import importlib.util
from core.cli.ui import console, print_error, print_success
from core.plugins.interface import Plugin


def export_manifest_cmd(plugin_name: str) -> int:
    """
    Generate a manifest.json file from a plugin's Python metadata definition.
    """
    plugin_dir = Path("plugins") / plugin_name

    if not plugin_dir.exists() or not plugin_dir.is_dir():
        print_error(f"Local plugin '{plugin_name}' not found.")
        return 1

    manifest_path = plugin_dir / "manifest.json"
    if manifest_path.exists():
        console.print(
            f"[yellow]Plugin '{plugin_name}' already has a manifest.json file.[/yellow]"
        )
        return 0

    plugin_file = plugin_dir / "plugin.py"
    if not plugin_file.exists():
        plugin_file = plugin_dir / "__init__.py"

    if not plugin_file.exists():
        print_error(
            f"No entry file (plugin.py or __init__.py) found for '{plugin_name}'."
        )
        return 1

    # Load the module minimally to extract metadata
    module_name = f"plugins.{plugin_name}.plugin"

    # Ensure plugins parent package exists in sys.modules
    plugins_root = plugin_dir.parent
    if "plugins" not in sys.modules:
        import types

        pkg = types.ModuleType("plugins")
        pkg.__path__ = [str(plugins_root)]
        pkg.__package__ = "plugins"
        sys.modules["plugins"] = pkg

    pkg_fqn = f"plugins.{plugin_name}"
    if pkg_fqn not in sys.modules:
        import types

        pkg = types.ModuleType(pkg_fqn)
        pkg.__path__ = [str(plugin_dir)]
        pkg.__package__ = pkg_fqn
        sys.modules[pkg_fqn] = pkg

    try:
        spec = importlib.util.spec_from_file_location(module_name, str(plugin_file))
        if spec is None or spec.loader is None:
            print_error(f"Failed to create module spec for '{plugin_name}'.")
            return 1

        module = importlib.util.module_from_spec(spec)
        module.__package__ = pkg_fqn
        sys.modules[module_name] = module

        # We need to mock dependencies that might cause import errors just for metadata extraction
        spec.loader.exec_module(module)

        # Find the Plugin class in the module
        plugin_class = None
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, Plugin)
                and attr is not Plugin
                and not getattr(attr, "__abstractmethods__", None)
            ):
                plugin_class = attr
                break

        if plugin_class is None:
            print_error(f"No Plugin subclass found in '{plugin_name}'.")
            return 1

        # Instantiate without initialize() just to get metadata
        plugin_instance = plugin_class()
        metadata = plugin_instance.metadata

        # Save to manifest.json
        metadata.to_json_file(manifest_path)
        print_success(f"Successfully exported manifest to [bold]{manifest_path}[/bold]")
        return 0

    except Exception as e:
        print_error(f"Error extracting metadata from '{plugin_name}': {e}")
        return 1
