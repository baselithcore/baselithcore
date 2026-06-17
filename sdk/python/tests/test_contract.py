"""Contract guard: the SDK's endpoints must exist in the exported OpenAPI schema.

Reads the checked-in ``sdk/openapi.json`` (produced by
``scripts/export_openapi.py``). Skipped when the schema is absent so the unit
suite stays runnable in isolation.
"""

import json
from pathlib import Path

import pytest

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "openapi.json"

# (method, path) pairs the SDK relies on. Health probes are unversioned; data
# endpoints use the /v1 alias.
_REQUIRED = [
    ("post", "/v1/chat"),
    ("post", "/v1/chat/stream"),
    ("post", "/v1/feedback"),
    ("get", "/health"),
    ("get", "/health/ready"),
]


@pytest.fixture(scope="module")
def schema():
    if not _SCHEMA_PATH.exists():
        pytest.skip(f"OpenAPI schema not found at {_SCHEMA_PATH}")
    return json.loads(_SCHEMA_PATH.read_text())


@pytest.mark.parametrize("method,path", _REQUIRED)
def test_sdk_endpoint_exists_in_schema(schema, method, path):
    paths = schema.get("paths", {})
    assert path in paths, f"{path} missing from OpenAPI schema"
    assert method in paths[path], f"{method.upper()} {path} missing from schema"
