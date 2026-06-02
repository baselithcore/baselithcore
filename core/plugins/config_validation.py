"""Plugin configuration validation against declared JSON Schemas.

A plugin may expose a JSON Schema from :meth:`Plugin.get_config_schema`. When it
does, the loader validates the user-supplied config against that schema *before*
calling :meth:`Plugin.initialize`, giving plugin authors early, precise feedback
instead of an opaque failure deep inside initialization.

Behaviour mirrors the signing/compat posture: validation always runs and reports
problems, but it is advisory by default. Set ``BASELITH_ENFORCE_PLUGIN_CONFIG``
to a truthy value to make an invalid config skip the plugin instead of merely
logging. A plugin that declares no schema (the default empty dict) is always a
no-op, so existing plugins are unaffected.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from core.observability.logging import get_logger

logger = get_logger(__name__)


def is_config_enforcement_enabled() -> bool:
    """Whether an invalid plugin config should block loading.

    When ``BASELITH_ENFORCE_PLUGIN_CONFIG`` is truthy the loader skips a plugin
    whose config fails its declared schema. Default (unset) is warn-only.
    """
    raw = os.environ.get("BASELITH_ENFORCE_PLUGIN_CONFIG", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def validate_plugin_config(
    schema: Dict[str, Any] | None,
    config: Dict[str, Any] | None,
) -> List[str]:
    """Validate a plugin config against its JSON Schema.

    Pure inspection — never raises; the caller decides whether to warn or skip
    based on :func:`is_config_enforcement_enabled`.

    Args:
        schema: JSON Schema from ``Plugin.get_config_schema()``. An empty or
            falsy schema means "no contract declared" and yields no problems.
        config: The configuration dict to validate.

    Returns:
        A list of human-readable validation problems; empty when valid (or when
        no schema is declared).
    """
    if not schema:
        return []

    try:
        from jsonschema import Draft7Validator
        from jsonschema.exceptions import SchemaError
    except ImportError:
        logger.warning(
            "jsonschema not installed; skipping plugin config validation. "
            "Install the core dependencies to enable schema enforcement."
        )
        return []

    try:
        Draft7Validator.check_schema(schema)
    except SchemaError as exc:
        return [f"invalid config schema declared by plugin: {exc.message}"]

    validator = Draft7Validator(schema)

    problems: List[str] = []
    for error in sorted(validator.iter_errors(config or {}), key=str):
        location = "/".join(str(p) for p in error.absolute_path) or "<root>"
        problems.append(f"config at '{location}': {error.message}")
    return problems
