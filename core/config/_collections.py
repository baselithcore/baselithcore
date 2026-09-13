"""Environment parsing for collection-typed settings.

pydantic-settings treats any ``list``/``set``/``dict`` field as *complex* and
JSON-decodes its raw environment value inside ``EnvSettingsSource`` — before
any ``field_validator`` runs. So a field that looks like it accepts a
comma-separated list does not: ``FOO=a,b`` raises ``SettingsError`` out of the
**whole settings class**, not just that field, and a class is usually a whole
subsystem's configuration. A blank value fails identically, which means an
operator who copies ``.env.example`` verbatim can break a subsystem by leaving
a key empty.

The fix has two halves and needs both:

1. ``Annotated[list[str], NoDecode]`` on the field, which tells
   pydantic-settings to hand the raw string through untouched;
2. a ``field_validator(mode="before")`` calling :func:`csv_list`, which then
   actually parses it.

:func:`csv_list` still accepts a JSON array, because deployments configured
against the previous behaviour have one in their environment (``.env.example``
itself ships ``TRUSTED_HOSTS=["app.example.com"]``) and ``NoDecode`` would
otherwise hand them ``['["app.example.com"]']``.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["csv_list"]


def csv_list(value: Any) -> Any:
    """Normalise an environment value to a list of non-empty strings.

    Args:
        value: The raw setting value. A string is parsed; anything else
            (an already-built list, a default) is returned unchanged so
            constructing the config in Python keeps working.

    Returns:
        A list of trimmed strings for a string input — empty for a blank or
        whitespace-only value — otherwise *value* unchanged.

    Examples:
        >>> csv_list("core.,plugins.")
        ['core.', 'plugins.']
        >>> csv_list("  ")
        []
        >>> csv_list('["a", "b"]')
        ['a', 'b']
    """
    if value is None:
        return []
    if not isinstance(value, str):
        return value

    text = value.strip()
    if not text:
        return []

    # Backwards compatibility: a JSON array is what pydantic-settings required
    # before NoDecode, so existing environments are full of them.
    if text[0] in "[":
        try:
            decoded = json.loads(text)
        except ValueError:
            pass
        else:
            if isinstance(decoded, list):
                return [str(item).strip() for item in decoded if str(item).strip()]

    return [item.strip() for item in text.split(",") if item.strip()]
