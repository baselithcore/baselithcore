"""``x-mcp-header``: mirroring tool parameters into ``Mcp-Param-*`` headers.

The point is that an intermediary can route or rate-limit on a parameter without
parsing the body. That only holds if the annotation is safe to mirror, so the
constraints are enforced at *registration*: a tool whose annotation is invalid
would otherwise be advertised and then rejected by every conforming client.

Only *statically reachable* properties qualify — a chain of ``properties`` keys
from the schema root. A value behind ``items``, ``oneOf`` or ``$ref`` has no
single position to read, so mirroring it would be ambiguous.
"""

from __future__ import annotations

import re
from typing import Any

# RFC 9110 §5.1 token, which is what an HTTP field name must be.
_TOKEN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_MIRRORABLE_TYPES = frozenset({"string", "integer", "boolean"})

HEADER_PREFIX = "Mcp-Param-"


class InvalidParamAnnotation(ValueError):
    """A tool declares an ``x-mcp-header`` that cannot be mirrored safely."""


def _walk(schema: Any, path: tuple[str, ...]) -> list[tuple[str, tuple[str, ...], Any]]:
    """Collect ``(annotation, property path, subschema)`` triples."""
    found: list[tuple[str, tuple[str, ...], Any]] = []
    if not isinstance(schema, dict):
        return found
    for name, subschema in (schema.get("properties") or {}).items():
        if not isinstance(subschema, dict):
            continue
        here = (*path, name)
        annotation = subschema.get("x-mcp-header")
        if annotation is not None:
            found.append((annotation, here, subschema))
        found.extend(_walk(subschema, here))
    return found


def _unreachable_annotations(schema: Any) -> bool:
    """Whether any ``x-mcp-header`` sits outside a pure ``properties`` chain."""
    reachable = {path for _, path, _ in _walk(schema, ())}

    def scan(node: Any, path: tuple[str, ...], in_properties: bool) -> bool:
        if isinstance(node, list):
            return any(scan(item, path, False) for item in node)
        if not isinstance(node, dict):
            return False
        if node.get("x-mcp-header") is not None and (
            not in_properties or path not in reachable
        ):
            return True
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                if any(scan(v, (*path, k), True) for k, v in value.items()):
                    return True
            elif key != "x-mcp-header" and scan(value, path, False):
                return True
        return False

    return scan(schema, (), True)


def validate_annotations(schema: Any) -> dict[str, tuple[str, ...]]:
    """Validate every ``x-mcp-header`` in *schema* and return the mapping.

    Returns:
        Header name → property path.

    Raises:
        InvalidParamAnnotation: An annotation is empty, not an HTTP token, on a
            non-primitive (or ``number``) property, duplicated case-insensitively,
            or attached to a property that is not statically reachable.
    """
    if _unreachable_annotations(schema):
        raise InvalidParamAnnotation(
            "x-mcp-header is only allowed on properties statically reachable "
            "from the schema root through `properties` keys"
        )

    mapping: dict[str, tuple[str, ...]] = {}
    seen: set[str] = set()
    for annotation, path, subschema in _walk(schema, ()):
        if not isinstance(annotation, str) or not annotation:
            raise InvalidParamAnnotation(
                f"x-mcp-header must be a non-empty string at {path}"
            )
        if not _TOKEN.match(annotation):
            raise InvalidParamAnnotation(
                f"x-mcp-header {annotation!r} is not a valid HTTP field-name token"
            )
        if annotation.lower() in seen:
            raise InvalidParamAnnotation(
                f"x-mcp-header {annotation!r} is not unique (names are "
                "case-insensitively unique within one inputSchema)"
            )
        declared = subschema.get("type")
        if declared not in _MIRRORABLE_TYPES:
            raise InvalidParamAnnotation(
                f"x-mcp-header {annotation!r} is on a {declared!r} property; only "
                "string, integer and boolean can be mirrored"
            )
        seen.add(annotation.lower())
        mapping[annotation] = path
    return mapping


def header_annotations(schema: Any) -> dict[str, tuple[str, ...]]:
    """Validated header name → property path mapping for *schema*."""
    return validate_annotations(schema)


def read_path(arguments: Any, path: tuple[str, ...]) -> Any:
    """Read the value at *path*, or None when any step is absent."""
    node = arguments
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def stringify(value: Any) -> str:
    """Render *value* the way the spec defines for a header."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


__all__ = [
    "HEADER_PREFIX",
    "InvalidParamAnnotation",
    "header_annotations",
    "read_path",
    "stringify",
    "validate_annotations",
]
