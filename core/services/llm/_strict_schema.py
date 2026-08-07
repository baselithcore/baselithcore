"""Make a JSON Schema satisfy strict structured-output enforcement.

Providers that enforce a schema exactly (OpenAI's ``json_schema`` response
format with ``strict: true``, and the OpenAI-compatible endpoints that copy
it) accept a **narrower** dialect than JSON Schema proper. Two rules bite
every Pydantic-derived schema:

* every key in ``properties`` must appear in ``required``;
* every object must set ``additionalProperties: false``;
* a ``$ref`` may not carry sibling keywords.

``model_json_schema()`` breaks the first rule for any field with a default —
``kind: ClaimKind = ClaimKind.FACT`` is simply absent from ``required`` — and
the request is rejected before a single token is generated::

    Invalid schema for response_format 'ClaimSet': 'required' is required to
    be supplied and to be an array including every key in properties.
    Missing 'kind'.

A defaulted field whose type is a nested model or enum fails a second way,
because Pydantic emits the default *beside* the reference::

    "kind": {"$ref": "#/$defs/ClaimKind", "default": "fact"}
    Invalid schema: $ref cannot have keywords {'default'}.

So a model that is perfectly reasonable in Python cannot be used as a
response model at all, which is a framework-level defect rather than
something each caller should work around by deleting its defaults.

Requiring a defaulted field costs nothing at validation time: the model now
always emits a value, so the default is simply never exercised. A field typed
``X | None`` keeps its ``null`` branch, which is the provider's own
recommended way to express "optional".
"""

from __future__ import annotations

from typing import Any

__all__ = ["to_strict_schema"]

# Keywords whose values are themselves schemas, or collections of schemas.
_SCHEMA_KEYS = ("items", "additionalItems", "contains", "not", "if", "then", "else")
_SCHEMA_LIST_KEYS = ("anyOf", "oneOf", "allOf", "prefixItems")
_SCHEMA_MAP_KEYS = ("$defs", "definitions", "properties", "patternProperties")


def to_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``schema`` that a strict enforcer will accept.

    Walks the whole document — including ``$defs``, which is where Pydantic
    puts every nested model — and, for each object schema, requires all of its
    declared properties and forbids extra ones. A reference is reduced to the
    bare ``$ref``. Non-object schemas are copied unchanged.

    Args:
        schema: A JSON Schema object, typically from ``model_json_schema()``.

    Returns:
        A new schema; the input is never mutated.
    """
    return _walk(schema)


def _walk(node: Any) -> Any:
    if isinstance(node, list):
        return [_walk(item) for item in node]
    if not isinstance(node, dict):
        return node

    # A reference carries no siblings here. Pydantic attaches `default` (and
    # sometimes `title`) to a field whose type is a nested model or enum, and
    # the enforcer rejects the whole request for it. The referenced schema is
    # the contract; the sibling only restated a Python-side default that the
    # model is now required to emit anyway.
    if "$ref" in node:
        return {"$ref": node["$ref"]}

    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in _SCHEMA_MAP_KEYS and isinstance(value, dict):
            out[key] = {name: _walk(sub) for name, sub in value.items()}
        elif key in _SCHEMA_LIST_KEYS and isinstance(value, list):
            out[key] = [_walk(sub) for sub in value]
        elif key in _SCHEMA_KEYS:
            out[key] = _walk(value)
        else:
            out[key] = value

    properties = out.get("properties")
    if isinstance(properties, dict):
        # Order is not significant to the enforcer, but a stable one keeps
        # request payloads (and their cache keys) reproducible.
        out["required"] = list(properties)
        out["additionalProperties"] = False

    return out
