"""The plugin-less ``/health/ready`` fallback is drain-aware too."""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import APIRouter, Response

from core.lifecycle import drain

_SHIM = Path(__file__).resolve().parents[4] / "core" / "routers" / "status.py"


def _load_fallback() -> Any:
    """Execute the shim as if ``plugins.api_routers`` were not installed."""
    spec = importlib.util.spec_from_file_location("_status_fallback", _SHIM)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with patch(
        "core.utils.optional_import.optional_router",
        return_value=(None, APIRouter()),
    ):
        spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _reset_drain() -> Iterator[None]:
    drain._reset_for_tests()
    yield
    drain._reset_for_tests()


def _readiness(module: Any) -> Any:
    for route in module.router.routes:
        if getattr(route, "path", None) == "/health/ready":
            return route.endpoint
    raise AssertionError("fallback router has no /health/ready")


async def test_fallback_is_ready_while_serving() -> None:
    response = Response()
    body = await _readiness(_load_fallback())(response)
    assert response.status_code == 200
    assert body["status"] == "ready"


async def test_fallback_answers_503_while_draining() -> None:
    endpoint = _readiness(_load_fallback())
    drain.mark_draining()
    response = Response()
    body = await endpoint(response)
    assert response.status_code == 503
    assert body["status"] == "draining"
