#!/usr/bin/env python3
"""
Migrate legacy plugins to use manifest.yaml metadata instead of the hardcoded `metadata` property.
Usage: python scripts/migrate_plugins.py <plugin_directory>
"""

import sys
import ast
import yaml
from pathlib import Path


def extract_value(node):
    """Extract a Python primitive value from an AST node."""
    if isinstance(node, ast.Constant):
        return node.value
    elif isinstance(node, ast.List):
        return [extract_value(elt) for elt in node.elts]
    elif isinstance(node, ast.Dict):
        return {
            extract_value(k): extract_value(v) for k, v in zip(node.keys, node.values)
        }
    return None


def extract_metadata_from_ast(plugin_file: Path):
    with open(plugin_file, "r", encoding="utf-8") as f:
        source_code = f.read()

    tree = ast.parse(source_code, filename=str(plugin_file))

    metadata_kwargs = {}
    metadata_node = None

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for body_node in node.body:
                if (
                    isinstance(body_node, ast.FunctionDef)
                    and body_node.name == "metadata"
                ):
                    metadata_node = body_node
                    break
            if metadata_node:
                break

    if not metadata_node:
        return None, None

    for node in metadata_node.body:
        if isinstance(node, ast.Return):
            if isinstance(node.value, ast.Call):
                call = node.value
                for keyword in call.keywords:
                    val = extract_value(keyword.value)
                    if val is not None and keyword.arg is not None:
                        metadata_kwargs[keyword.arg] = val

                # Positional args?
                if call.args:
                    if len(call.args) > 0:
                        metadata_kwargs["name"] = extract_value(call.args[0])
                    if len(call.args) > 1:
                        metadata_kwargs["version"] = extract_value(call.args[1])
            break

    return metadata_kwargs, (metadata_node.lineno, metadata_node.end_lineno)


def migrate_plugin(plugin_dir: Path):
    print(f"Checking plugin: {plugin_dir.name}")
    plugin_file = plugin_dir / "plugin.py"
    if not plugin_file.exists():
        plugin_file = plugin_dir / "__init__.py"

    if not plugin_file.exists():
        print("  No plugin.py or __init__.py found.")
        return False

    manifest_path = plugin_dir / "manifest.yaml"
    if manifest_path.exists():
        print("  manifest.yaml already exists. Skipping.")
        return False

    metadata, lines_to_remove = extract_metadata_from_ast(plugin_file)
    if not metadata:
        print(
            f"  Could not extract metadata from {plugin_file.name}. No legacy metadata property found."
        )
        return False

    # Write manifest.yaml
    with open(manifest_path, "w", encoding="utf-8") as f:
        yaml.dump(metadata, f, sort_keys=False, default_flow_style=False)
    print(f"  Created manifest.yaml with: {metadata.get('name', 'unknown')}")

    # Remove the metadata method
    with open(plugin_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    start_line, end_line = lines_to_remove
    # We want to remove any decorators above the method too, like @property
    # Let's just find the @property on the line before
    if start_line > 1 and "@property" in lines[start_line - 2]:
        start_line -= 1

    # Remove lines from start_line to end_line (1-indexed)
    new_lines = lines[: start_line - 1] + lines[end_line:]

    # Also remove the PluginMetadata import if it exists
    new_lines_no_import = []
    for line in new_lines:
        if "from core.plugins import" in line and "PluginMetadata" in line:
            line = (
                line.replace(", PluginMetadata", "")
                .replace("PluginMetadata, ", "")
                .replace("PluginMetadata", "")
            )
            if "import  \n" in line or "import \n" in line:  # if it was the only import
                continue
        new_lines_no_import.append(line)

    with open(plugin_file, "w", encoding="utf-8") as f:
        f.writelines(new_lines_no_import)

    print(f"  Removed obsolete metadata method from {plugin_file.name}")
    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/migrate_plugins.py <directory>")
        sys.exit(1)

    target_dir = Path(sys.argv[1])
    if not target_dir.is_dir():
        print(f"{target_dir} is not a valid directory.")
        sys.exit(1)

    migrated = 0

    # Check if target_dir is a single plugin
    if (target_dir / "plugin.py").exists() or (target_dir / "__init__.py").exists():
        if migrate_plugin(target_dir):
            migrated += 1
    else:
        # Treat as a directory containing multiple plugins
        for subdir in target_dir.iterdir():
            if subdir.is_dir() and not subdir.name.startswith((".", "_")):
                if migrate_plugin(subdir):
                    migrated += 1

    print(f"\nMigration complete. Migrated {migrated} plugins.")
