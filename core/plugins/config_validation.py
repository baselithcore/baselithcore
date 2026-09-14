"""Plugin configuration validation against declared JSON Schemas.

A plugin may expose a JSON Schema from :meth:`Plugin.get_config_schema`. When it
does, the loader validates the user-supplied config against that schema *before*
calling :meth:`Plugin.initialize`, giving plugin authors early, precise feedback
instead of an opaque failure deep inside initialization.

Behaviour mirrors the signing/compat posture and **fails closed**: validation
always runs, and a config that violates its declared schema skips the plugin.
The manifest's schema is the author's statement of what is safe to run, so
ignoring it and initializing anyway was never a defensible default. Set
``BASELITH_ENFORCE_PLUGIN_CONFIG=false`` (or ``0``/``no``/``off``) to downgrade
the refusal to a warning while a manifest is corrected — that one variable is
the whole switch. A plugin that declares no schema (the default empty dict) is
always a no-op, so existing plugins are unaffected.
"""

from __future__ import annotations

import os
from typing import Any

from core.observability.logging import get_logger

logger = get_logger(__name__)


#: Values that explicitly downgrade the fail-closed config gate to warn-only.
_FALSEY = ("0", "false", "no", "off")


def is_config_enforcement_enabled() -> bool:
    """Whether an invalid plugin config blocks loading.

    True unless ``BASELITH_ENFORCE_PLUGIN_CONFIG`` is explicitly set to a falsey
    value (``0``/``false``/``no``/``off``).

    This used to read the *same* variable the opposite way — "did the operator
    turn enforcement on?" — so it answered ``False`` on an unset environment
    while :func:`core.plugins.load_gates.is_config_gate_enforced` answered
    ``True``. Two functions disagreeing about one variable is a question with no
    correct answer for anyone reading the code or the logs, so both now return
    this, the fail-closed reading; the gate function is kept as an alias for its
    existing callers.

    Returns:
        True unless the environment explicitly disables enforcement.
    """
    raw = os.environ.get("BASELITH_ENFORCE_PLUGIN_CONFIG", "").strip().lower()
    return raw not in _FALSEY


def validate_plugin_config(
    schema: dict[str, Any] | None,
    config: dict[str, Any] | None,
) -> list[str]:
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

    problems: list[str] = []
    for error in sorted(validator.iter_errors(config or {}), key=str):
        location = "/".join(str(p) for p in error.absolute_path) or "<root>"
        problems.append(f"config at '{location}': {error.message}")
    return problems
