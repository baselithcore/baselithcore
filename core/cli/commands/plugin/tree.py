"""
Plugin dependency tree visualization.

Renders an interactive dependency graph using Rich Tree,
showing inter-plugin relationships and satisfaction status.
"""

import json
from pathlib import Path
from typing import Optional

import yaml
from rich.tree import Tree
from rich.text import Text

from core.cli.ui import console, print_error


def _load_all_manifests() -> dict[str, dict]:
    """Load manifests for all installed plugins."""
    plugins_dir = Path("plugins")
    manifests: dict[str, dict] = {}

    if not plugins_dir.exists():
        return manifests

    for plugin_dir in plugins_dir.iterdir():
        if (
            not plugin_dir.is_dir()
            or plugin_dir.name.startswith(".")
            or plugin_dir.name == "__pycache__"
        ):
            continue

        for ext in [".yaml", ".yml", ".json"]:
            manifest_path = plugin_dir / f"manifest{ext}"
            if manifest_path.exists():
                try:
                    with open(manifest_path, "r", encoding="utf-8") as f:
                        if ext in (".yaml", ".yml"):
                            data = yaml.safe_load(f) or {}
                        else:
                            data = json.load(f)
                    manifests[plugin_dir.name] = data
                except Exception:
                    manifests[plugin_dir.name] = {}
                break
        else:
            # No manifest, still register the plugin
            manifests[plugin_dir.name] = {}

    return manifests


def _build_tree_node(
    plugin_name: str,
    manifests: dict[str, dict],
    visited: set[str],
    parent: Tree,
) -> None:
    """Recursively build a dependency tree node."""
    manifest = manifests.get(plugin_name, {})
    plugin_deps = manifest.get("plugin_dependencies", [])
    python_deps = manifest.get("python_dependencies", [])

    for dep in plugin_deps:
        is_installed = dep in manifests
        dep_ver = manifests.get(dep, {}).get("version", "?")

        if is_installed:
            label = Text()
            label.append("✅ ", style="green")
            label.append(dep, style="bold")
            label.append(f" v{dep_ver}", style="dim")
        else:
            label = Text()
            label.append("❌ ", style="red")
            label.append(dep, style="bold red")
            label.append(" (missing)", style="dim red")

        child = parent.add(label)

        # Recurse (avoid cycles)
        if dep not in visited and is_installed:
            visited.add(dep)
            _build_tree_node(dep, manifests, visited, child)

    # Show Python deps as leaves
    for dep in python_deps:
        from importlib.metadata import distribution, PackageNotFoundError

        try:
            dist = distribution(dep)
            pkg_ver = dist.version
            label = Text()
            label.append("📦 ", style="dim")
            label.append(dep, style="dim cyan")
            label.append(f" v{pkg_ver}", style="dim")
        except PackageNotFoundError:
            label = Text()
            label.append("📦 ", style="dim red")
            label.append(dep, style="bold red")
            label.append(" (not installed)", style="dim red")
        parent.add(label)


def plugin_tree(plugin_name: Optional[str] = None, json_output: bool = False) -> int:
    """
    Display plugin dependency tree.

    Without a name, shows the full dependency graph for all plugins.
    With a name, shows only the dependency chain for that plugin.

    Args:
        plugin_name: Optional plugin name to focus on.
        json_output: Output a JSON adjacency list instead.

    Returns:
        Exit code.
    """
    manifests = _load_all_manifests()

    if not manifests:
        console.print("[yellow]No plugins found.[/yellow]")
        return 0

    if plugin_name and plugin_name not in manifests:
        print_error(f"Plugin '{plugin_name}' not found.")
        return 1

    if json_output:
        adjacency: dict[str, dict] = {}
        for name, manifest in manifests.items():
            if plugin_name and name != plugin_name:
                continue
            adjacency[name] = {
                "version": manifest.get("version", "?"),
                "plugin_dependencies": manifest.get("plugin_dependencies", []),
                "python_dependencies": manifest.get("python_dependencies", []),
            }
        print(json.dumps(adjacency, indent=2))
        return 0

    if plugin_name:
        # Single plugin tree
        manifest = manifests[plugin_name]
        version = manifest.get("version", "?")

        root_label = Text()
        root_label.append("🔌 ", style="bold")
        root_label.append(plugin_name, style="bold cyan")
        root_label.append(f" v{version}", style="dim")

        tree = Tree(root_label)
        _build_tree_node(plugin_name, manifests, {plugin_name}, tree)
    else:
        # Full tree
        tree = Tree(
            Text("🏗️  Baselith Plugin Ecosystem", style="bold blue"),
            guide_style="dim",
        )

        for name in sorted(manifests.keys()):
            manifest = manifests[name]
            version = manifest.get("version", "?")
            readiness = manifest.get("readiness", "stable")
            tags = manifest.get("tags", [])

            # Determine status
            is_disabled = (Path("plugins") / name / "plugin.disabled").exists()
            if is_disabled:
                icon = "⏸️"
                name_style = "dim"
            elif readiness == "alpha":
                icon = "🧪"
                name_style = "yellow"
            elif readiness == "beta":
                icon = "🔧"
                name_style = "cyan"
            else:
                icon = "✅"
                name_style = "bold green"

            label = Text()
            label.append(f"{icon} ", style="bold")
            label.append(name, style=name_style)
            label.append(f" v{version}", style="dim")
            if tags:
                label.append(f"  [{', '.join(tags)}]", style="dim magenta")

            branch = tree.add(label)
            _build_tree_node(name, manifests, {name}, branch)

    console.print()
    console.print(tree)
    console.print()
    return 0
