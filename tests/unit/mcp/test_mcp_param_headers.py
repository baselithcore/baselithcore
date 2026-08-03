"""`x-mcp-header`: tool parameters mirrored into `Mcp-Param-*` HTTP headers.

The point of mirroring is that intermediaries can route on a parameter without
parsing the body. That is only safe if header and body agree, so the server
rejects any divergence — and refuses to advertise an annotation that cannot be
mirrored safely in the first place.
"""

from __future__ import annotations

import base64

import pytest

from core.mcp.http_headers import extract_param_headers, validate_param_headers
from core.mcp.param_headers import (
    InvalidParamAnnotation,
    header_annotations,
    validate_annotations,
)
from core.mcp.server import MCPServer

_SCHEMA = {
    "type": "object",
    "properties": {
        "region": {"type": "string", "x-mcp-header": "Region"},
        "query": {"type": "string"},
    },
    "required": ["region", "query"],
}


class TestAnnotationValidation:
    def test_valid_annotation_is_collected(self) -> None:
        assert header_annotations(_SCHEMA) == {"Region": ("region",)}

    def test_nested_properties_are_reachable(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "target": {
                    "type": "object",
                    "properties": {
                        "zone": {"type": "string", "x-mcp-header": "Zone"},
                    },
                }
            },
        }

        assert header_annotations(schema) == {"Zone": ("target", "zone")}

    @pytest.mark.parametrize(
        ("annotation", "prop"),
        [
            ("", {"type": "string"}),
            ("Bad Header", {"type": "string"}),
            ("Line\nBreak", {"type": "string"}),
            ("Amount", {"type": "number"}),
            ("Blob", {"type": "object"}),
        ],
    )
    def test_invalid_annotations_are_rejected(
        self, annotation: str, prop: dict
    ) -> None:
        schema = {
            "type": "object",
            "properties": {"p": {**prop, "x-mcp-header": annotation}},
        }

        with pytest.raises(InvalidParamAnnotation):
            validate_annotations(schema)

    def test_duplicate_names_are_rejected_case_insensitively(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "a": {"type": "string", "x-mcp-header": "Zone"},
                "b": {"type": "string", "x-mcp-header": "zone"},
            },
        }

        with pytest.raises(InvalidParamAnnotation, match="unique"):
            validate_annotations(schema)

    def test_annotation_under_an_array_is_rejected(self) -> None:
        """Only statically reachable properties can be mirrored."""
        schema = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "zone": {"type": "string", "x-mcp-header": "Zone"}
                        },
                    },
                }
            },
        }

        with pytest.raises(InvalidParamAnnotation, match="reachable"):
            validate_annotations(schema)

    def test_registration_refuses_an_invalid_annotation(self) -> None:
        server = MCPServer()

        async def handler(p: str) -> str:
            return p

        with pytest.raises(InvalidParamAnnotation):
            server.register_tool(
                name="bad",
                description="",
                input_schema={
                    "type": "object",
                    "properties": {"p": {"type": "number", "x-mcp-header": "P"}},
                },
                handler=handler,
            )


class TestExtraction:
    def test_headers_are_derived_from_the_arguments(self) -> None:
        headers = extract_param_headers(
            _SCHEMA, {"region": "us-west1", "query": "SELECT 1"}
        )

        assert headers == {"Mcp-Param-Region": "us-west1"}

    def test_absent_values_omit_the_header(self) -> None:
        assert extract_param_headers(_SCHEMA, {"query": "SELECT 1"}) == {}

    def test_non_ascii_values_use_the_sentinel(self) -> None:
        headers = extract_param_headers(_SCHEMA, {"region": "münchen"})

        encoded = base64.b64encode("münchen".encode()).decode()
        assert headers["Mcp-Param-Region"] == f"=?base64?{encoded}?="

    def test_booleans_and_integers_stringify_predictably(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "flag": {"type": "boolean", "x-mcp-header": "Flag"},
                "count": {"type": "integer", "x-mcp-header": "Count"},
            },
        }

        headers = extract_param_headers(schema, {"flag": True, "count": 42})

        assert headers == {"Mcp-Param-Flag": "true", "Mcp-Param-Count": "42"}


class TestServerValidation:
    def test_matching_header_passes(self) -> None:
        validate_param_headers(
            {"mcp-param-region": "us-west1"},
            _SCHEMA,
            {"region": "us-west1", "query": "SELECT 1"},
        )

    def test_missing_header_is_rejected(self) -> None:
        from core.mcp.errors import HeaderMismatch

        with pytest.raises(HeaderMismatch, match="Mcp-Param-Region"):
            validate_param_headers(
                {}, _SCHEMA, {"region": "us-west1", "query": "SELECT 1"}
            )

    def test_diverging_header_is_rejected(self) -> None:
        from core.mcp.errors import HeaderMismatch

        with pytest.raises(HeaderMismatch):
            validate_param_headers(
                {"mcp-param-region": "eu-west1"},
                _SCHEMA,
                {"region": "us-west1", "query": "SELECT 1"},
            )

    def test_integers_compare_numerically(self) -> None:
        """`42.0` and `42` are the same value, not two different headers."""
        schema = {
            "type": "object",
            "properties": {"count": {"type": "integer", "x-mcp-header": "Count"}},
        }

        validate_param_headers({"mcp-param-count": "42.0"}, schema, {"count": 42})

    def test_absent_argument_expects_no_header(self) -> None:
        validate_param_headers({}, _SCHEMA, {"query": "SELECT 1"})
