"""Decide when a typed agent's tool schema can be sent ``strict``.

``LLMToolSpec.strict`` asks the provider to constrain the arguments it emits
to the schema exactly (Anthropic strict tool use, OpenAI strict function
calling); providers without the feature ignore the flag. The typed
:class:`~core.agent.agent.Agent` never set it, so every call was a best-effort
guess the local validator had to bounce back to the model.

Strict mode accepts a narrower JSON Schema dialect, and a request carrying a
schema outside it is rejected outright — a worse failure than a malformed
call. So the flag is only set when the schema already *means* what the strict
dialect would make it mean:

* every object lists all of its properties in ``required`` (an optional
  argument would silently become mandatory under strict);
* every object forbids extra keys — explicitly, or implicitly for a schema
  inferred from a signature, which the argument validator already closes to
  unknown keys;
* every property is typed, and only keywords both providers' strict modes
  accept appear anywhere in the document.

Anything else is sent exactly as before, non-strict.
"""

from __future__ import annotations

from typing import Any, Final

from core.services.llm._strict_schema import to_strict_schema

__all__ = ["strict_tool_parameters"]

#: Keywords accepted by both providers' strict modes. Conservative on
#: purpose: a keyword missing here costs strictness for one tool, a keyword
#: wrongly present costs the whole request.
_ALLOWED_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "description",
        "title",
        "anyOf",
        "$ref",
        "$defs",
        "definitions",
    }
)
_TYPED_BY: Final[tuple[str, ...]] = ("type", "anyOf", "$ref", "enum", "const")


def strict_tool_parameters(
    parameters: dict[str, Any], *, inferred: bool
) -> dict[str, Any] | None:
    """The strict form of a tool's parameter schema, or ``None`` to stay lax.

    Args:
        parameters: The tool's JSON-Schema object.
        inferred: Whether the schema was inferred from the callable's
            signature (closed to unknown keys by the validator) rather than
            declared by the tool author.

    Returns:
        dict[str, Any] | None: A copy carrying ``additionalProperties: false``
        on every object, or ``None`` when the schema does not fit the strict
        dialect without changing what it accepts.
    """
    if not isinstance(parameters, dict) or parameters.get("type") != "object":
        return None
    if not _compatible(parameters, inferred=inferred):
        return None
    return to_strict_schema(parameters)


def _compatible(node: Any, *, inferred: bool) -> bool:
    if isinstance(node, list):
        return all(_compatible(item, inferred=inferred) for item in node)
    if not isinstance(node, dict):
        return True
    if not set(node) <= _ALLOWED_KEYWORDS:
        return False
    if "$ref" in node:
        return set(node) <= {"$ref", "description", "title"}
    if not _object_ok(node, inferred=inferred):
        return False
    if node.get("type") == "array" and "items" not in node:
        return False
    for key in ("$defs", "definitions", "properties"):
        sub = node.get(key)
        if sub is not None:
            if not isinstance(sub, dict):
                return False
            if key == "properties" and not all(
                isinstance(p, dict) and any(k in p for k in _TYPED_BY)
                for p in sub.values()
            ):
                return False
            if not all(_compatible(s, inferred=inferred) for s in sub.values()):
                return False
    for key in ("items", "anyOf"):
        if key in node and not _compatible(node[key], inferred=inferred):
            return False
    return True


def _object_ok(node: dict[str, Any], *, inferred: bool) -> bool:
    """An object schema must require every property and forbid extras."""
    is_object = node.get("type") == "object" or "properties" in node
    if not is_object:
        return True
    properties = node.get("properties")
    if not isinstance(properties, dict):
        # A bare ``{"type": "object"}`` accepts anything; strict would not.
        return False
    if set(node.get("required") or ()) != set(properties):
        return False
    extra = node.get("additionalProperties")
    if extra is False:
        return True
    return extra is None and inferred
