"""
Static AST helpers for plugin discovery.

Pure functions used by :class:`core.plugins.resource_analyzer.ResourceAnalyzer`
to inspect plugin sources without importing them (restricted literal-only
evaluation — no code execution). Extracted to keep modules under the
500-line cap.
"""

from __future__ import annotations

import ast
from typing import Any


def base_name(node: ast.expr) -> str | None:
    """Extract the simple class/base name from an AST node."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def find_plugin_class(module_ast: ast.Module) -> ast.ClassDef | None:
    """Find the first class that looks like a plugin implementation."""
    plugin_bases = {"Plugin", "AgentPlugin", "RouterPlugin", "GraphPlugin"}

    for node in module_ast.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if any(base_name(base) in plugin_bases for base in node.bases):
            return node
    return None


def get_method_node(
    class_node: ast.ClassDef, method_name: str
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Return a class method node by name."""
    for node in class_node.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == method_name:
                return node
    return None


def literal_return_value(class_node: ast.ClassDef, method_name: str) -> Any | None:
    """Extract a literal return value from a simple method implementation."""
    method_node = get_method_node(class_node, method_name)
    if method_node is None:
        return None

    for stmt in method_node.body:
        if isinstance(stmt, ast.Return) and stmt.value is not None:
            try:
                return static_eval(stmt.value)
            except Exception:
                return None
    return None


def static_eval(node: ast.AST) -> Any:
    """Evaluate a restricted subset of AST nodes used by plugin metadata."""
    builtin_names = {
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
        "list": list,
        "dict": dict,
    }

    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List):
        return [static_eval(item) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(static_eval(item) for item in node.elts)
    if isinstance(node, ast.Dict):
        result = {}
        for key, value in zip(node.keys, node.values, strict=False):
            if key is not None:
                result[static_eval(key)] = static_eval(value)
        return result
    if isinstance(node, ast.Name) and node.id in builtin_names:
        return builtin_names[node.id]

    raise ValueError(
        f"Unsupported AST node for static evaluation: {type(node).__name__}"
    )


def dict_return_keys(class_node: ast.ClassDef, method_name: str) -> list[str]:
    """Extract string keys from a returned dict even when values are dynamic."""
    method_node = get_method_node(class_node, method_name)
    if method_node is None:
        return []

    for stmt in method_node.body:
        if not isinstance(stmt, ast.Return):
            continue
        if not isinstance(stmt.value, ast.Dict):
            return []

        keys: list[str] = []
        for key_node in stmt.value.keys:
            if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                keys.append(key_node.value)
            else:
                return []
        return keys

    return []


def dict_by_key(items: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    """Index a list of dict items by a string key."""
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        if isinstance(item, dict):
            item_key = item.get(key)
            if isinstance(item_key, str):
                result[item_key] = item
    return result


def match_config_key(
    plugin_configs: dict[str, dict[str, Any]],
    directory_name: str,
    metadata_name: str,
) -> str | None:
    """Resolve a plugin config key against directory and metadata aliases."""
    candidates = (
        directory_name,
        metadata_name,
        directory_name.replace("_", "-"),
        directory_name.replace("-", "_"),
    )
    for candidate in candidates:
        if candidate in plugin_configs:
            return candidate
    return None


__all__ = [
    "base_name",
    "dict_by_key",
    "dict_return_keys",
    "find_plugin_class",
    "get_method_node",
    "literal_return_value",
    "match_config_key",
    "static_eval",
]
