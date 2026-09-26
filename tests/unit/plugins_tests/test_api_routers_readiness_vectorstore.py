"""The /health/ready vector store probe distinguishes missing from unreachable."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from plugins.api_routers.status import _check_vectorstore


def _service(exists: AsyncMock) -> SimpleNamespace:
    return SimpleNamespace(
        provider=SimpleNamespace(collection_exists=exists),
        config=SimpleNamespace(collection_name="documents"),
    )


async def _probe(exists: AsyncMock) -> bool:
    with patch(
        "core.services.vectorstore.service.get_vectorstore_service",
        return_value=_service(exists),
    ):
        return await _check_vectorstore()


async def test_reachable_with_collection_is_ready() -> None:
    assert await _probe(AsyncMock(return_value=True)) is True


async def test_missing_collection_is_not_ready() -> None:
    assert await _probe(AsyncMock(return_value=False)) is False


async def test_unreachable_store_is_not_ready() -> None:
    assert await _probe(AsyncMock(side_effect=ConnectionError("down"))) is False


async def test_provider_without_probe_reports_ready() -> None:
    service = SimpleNamespace(
        provider=SimpleNamespace(), config=SimpleNamespace(collection_name="d")
    )
    with patch(
        "core.services.vectorstore.service.get_vectorstore_service",
        return_value=service,
    ):
        assert await _check_vectorstore() is True
