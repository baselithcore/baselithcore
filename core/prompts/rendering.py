"""
Safe prompt template rendering.

Templates use ``{{ variable }}`` placeholders. Substitution is a literal
string replacement — there is no expression evaluation, attribute access, or
format-spec handling (unlike ``str.format``/f-strings), so a template or a
variable value can never reach code execution or leak object internals. Unknown
``{{ ... }}`` placeholders left after substitution are reported, and (optionally)
declared-but-missing variables are rejected.
"""

from __future__ import annotations

import re
from typing import Any

from core.prompts.types import PromptRenderError

# Matches {{ name }} with optional surrounding whitespace; name is a simple
# identifier (letters, digits, underscore, dot for namespacing).
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\}\}")


def find_placeholders(template: str) -> list[str]:
    """Return the distinct variable names referenced in ``template``."""
    seen: list[str] = []
    for match in _PLACEHOLDER.finditer(template):
        name = match.group(1)
        if name not in seen:
            seen.append(name)
    return seen


def render_template(
    template: str,
    variables: dict[str, Any],
    *,
    strict: bool = True,
) -> str:
    """Render ``template`` by substituting ``{{ var }}`` placeholders.

    Args:
        template: The template body.
        variables: Mapping of variable name to value (stringified on insert).
        strict: When ``True``, raise if the template references a variable not
            present in ``variables``. When ``False``, leave such placeholders
            untouched.

    Raises:
        PromptRenderError: In strict mode, when a referenced variable is missing.
    """
    missing: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in variables:
            return str(variables[name])
        if strict:
            missing.append(name)
        return match.group(0)

    result = _PLACEHOLDER.sub(_replace, template)
    if strict and missing:
        raise PromptRenderError(
            f"Missing variables for prompt render: {sorted(set(missing))}"
        )
    return result
