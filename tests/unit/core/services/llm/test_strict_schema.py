"""The strict-schema adapter for structured outputs.

Regression origin: a plugin's response model carried a field with a default
(`kind: ClaimKind = ClaimKind.FACT`). Pydantic leaves such a field out of
`required`, and the request was rejected before generation:

    Invalid schema for response_format 'ClaimSet': 'required' is required to
    be supplied and to be an array including every key in properties.
    Missing 'kind'.
"""

from __future__ import annotations

from typing import Any

from core.services.llm._strict_schema import to_strict_schema


def test_a_defaulted_field_becomes_required() -> None:
    from enum import StrEnum

    from pydantic import BaseModel, Field

    class Kind(StrEnum):
        FACT = "fact"
        OWN = "own_analysis"

    class Claim(BaseModel):
        id: str
        text: str = Field(min_length=3)
        kind: Kind = Kind.FACT

    strict = to_strict_schema(Claim.model_json_schema())

    assert set(strict["required"]) == {"id", "text", "kind"}
    assert strict["additionalProperties"] is False


def test_nested_models_under_defs_are_adapted_too() -> None:
    from pydantic import BaseModel

    class Section(BaseModel):
        heading: str
        notes: str = ""

    class Outline(BaseModel):
        title: str
        sections: list[Section]

    strict = to_strict_schema(Outline.model_json_schema())

    section = strict["$defs"]["Section"]
    assert set(section["required"]) == {"heading", "notes"}
    assert section["additionalProperties"] is False


def test_optional_fields_keep_their_null_branch() -> None:
    """`X | None` stays nullable — it is required to be *present*, not set."""
    from pydantic import BaseModel

    class Run(BaseModel):
        id: str
        pr_url: str | None = None

    strict = to_strict_schema(Run.model_json_schema())

    assert "pr_url" in strict["required"]
    branches = strict["properties"]["pr_url"]["anyOf"]
    assert {"type": "null"} in branches


def test_the_input_schema_is_not_mutated() -> None:
    original: dict[str, Any] = {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "required": ["a"],
    }
    before = str(original)

    to_strict_schema(original)

    assert str(original) == before


def test_schemas_without_properties_pass_through() -> None:
    """A bare array or scalar schema has nothing to require."""
    schema = {"type": "array", "items": {"type": "string"}}

    assert to_strict_schema(schema) == schema


def test_branch_schemas_are_walked() -> None:
    """anyOf/oneOf carry object schemas that need the same treatment."""
    schema = {
        "anyOf": [
            {"type": "object", "properties": {"a": {"type": "string"}}},
            {"type": "null"},
        ]
    }

    strict = to_strict_schema(schema)

    assert strict["anyOf"][0]["required"] == ["a"]
    assert strict["anyOf"][1] == {"type": "null"}


def test_a_ref_loses_its_sibling_keywords() -> None:
    """Regression: `{"$ref": ..., "default": ...}` is rejected outright.

    Pydantic emits the default beside the reference for a defaulted field
    whose type is a nested model or enum:

        Invalid schema: $ref cannot have keywords {'default'}.
    """
    from enum import StrEnum

    from pydantic import BaseModel

    class Kind(StrEnum):
        FACT = "fact"

    class Claim(BaseModel):
        id: str
        kind: Kind = Kind.FACT

    strict = to_strict_schema(Claim.model_json_schema())

    assert strict["properties"]["kind"] == {"$ref": "#/$defs/Kind"}
    assert "kind" in strict["required"]
