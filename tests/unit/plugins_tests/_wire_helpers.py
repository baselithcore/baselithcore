"""Wire-equivalence helpers for the api_routers response models.

A ``response_model`` filters and re-serialises what a route returns. The
models added for OpenAPI must not change a single key or value, so these
helpers capture what the endpoint function itself returned (the input to
FastAPI's ``serialize_response``) and compare the body on the wire with the
body FastAPI produced before the model existed — ``jsonable_encoder`` of that
same return value. Both sides are rendered with ``json.dumps`` without key
sorting, so key order and int/float spelling are compared too.
"""

from __future__ import annotations

import json
from typing import Any

import fastapi.routing
import pytest
from fastapi.encoders import jsonable_encoder


class WireSpy:
    """Records every value an endpoint returned, in call order."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.returned: list[Any] = []
        original = fastapi.routing.serialize_response

        async def spy(**kwargs: Any) -> Any:
            self.returned.append(kwargs["response_content"])
            return await original(**kwargs)

        monkeypatch.setattr(fastapi.routing, "serialize_response", spy)

    def assert_unchanged(self, response: Any) -> None:
        """The last response body is byte-for-byte the pre-model rendering."""
        assert self.returned, "endpoint returned nothing through serialize_response"
        before = json.dumps(jsonable_encoder(self.returned[-1]))
        after = json.dumps(response.json())
        assert after == before, f"{after}\n!=\n{before}"


def typed_2xx_schema(openapi: dict[str, Any], path: str, method: str) -> dict[str, Any]:
    """The JSON schema an operation documents for its 2xx response."""
    responses = openapi["paths"][path][method]["responses"]
    (code,) = [c for c in responses if c.startswith("2")]
    schema: dict[str, Any] = responses[code]["content"]["application/json"]["schema"]
    return schema


def assert_typed(openapi: dict[str, Any], path: str, method: str) -> None:
    """The 2xx schema names a component model, not a bare object."""
    schema = typed_2xx_schema(openapi, path, method)
    target = schema.get("items", schema)
    assert "$ref" in target, (path, method, schema)


def assert_problem_documented(
    openapi: dict[str, Any], path: str, method: str, status: str
) -> None:
    """``status`` is documented as an RFC 9457 problem document."""
    response = openapi["paths"][path][method]["responses"][status]
    schema = response["content"]["application/problem+json"]["schema"]
    assert schema == {"$ref": "#/components/schemas/ProblemDetails"}
