"""Minimal RFC 6570 Level-1 URI templates for MCP resource templates.

Only simple string expansion (``{var}``) is supported — the level MCP resource
templates use in practice. A variable matches one path segment, so
``mcp://reports/{year}/{month}`` never swallows the separator and collapses two
distinct resources into one.
"""

from __future__ import annotations

import re

_VARIABLE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def compile_template(template: str) -> re.Pattern[str]:
    """Compile *template* into an anchored pattern with named groups.

    Raises:
        ValueError: The template declares the same variable twice, which would
            make the extracted values ambiguous.
    """
    names = _VARIABLE.findall(template)
    if len(names) != len(set(names)):
        raise ValueError(f"Duplicate variable in URI template: {template}")

    pattern = ""
    position = 0
    for match in _VARIABLE.finditer(template):
        pattern += re.escape(template[position : match.start()])
        pattern += f"(?P<{match.group(1)}>[^/]+)"
        position = match.end()
    pattern += re.escape(template[position:])
    return re.compile(f"^{pattern}$")


def match_template(pattern: re.Pattern[str], uri: str) -> dict[str, str] | None:
    """Return the variables *uri* binds, or None when it does not match."""
    match = pattern.match(uri)
    return match.groupdict() if match else None


__all__ = ["compile_template", "match_template"]
