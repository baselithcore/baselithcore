"""Provider status mapping and shared-client lifecycle for fine-tuning."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from core.finetuning.models import TrainingStatus
from core.finetuning.providers import (
    PROVIDER_TIMEOUT_S,
    OpenAIProvider,
    TogetherProvider,
    map_status,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("validating_files", TrainingStatus.VALIDATING),
        ("queued", TrainingStatus.QUEUED),
        ("running", TrainingStatus.RUNNING),
        ("succeeded", TrainingStatus.SUCCEEDED),
        ("failed", TrainingStatus.FAILED),
        ("cancelled", TrainingStatus.CANCELLED),
    ],
)
def test_openai_status_map(raw: str, expected: TrainingStatus) -> None:
    assert map_status("openai", raw) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("pending", TrainingStatus.PENDING),
        ("completed", TrainingStatus.SUCCEEDED),
        ("error", TrainingStatus.FAILED),
        ("user_error", TrainingStatus.FAILED),
        ("cancel_requested", TrainingStatus.CANCELLED),
        ("compressing", TrainingStatus.RUNNING),
    ],
)
def test_together_status_map(raw: str, expected: TrainingStatus) -> None:
    assert map_status("together", raw) is expected


@pytest.mark.parametrize("raw", ["brand_new_state", None, ""])
def test_unknown_status_is_pending_not_error(raw: str | None) -> None:
    assert map_status("openai", raw) is TrainingStatus.PENDING
    assert map_status("together", raw) is TrainingStatus.PENDING


async def test_openai_get_status_maps_validating_files() -> None:
    provider = OpenAIProvider(api_key="k")
    job = SimpleNamespace(
        id="ft-1",
        model="gpt-4o-mini",
        status="validating_files",
        fine_tuned_model=None,
        trained_tokens=None,
        error=None,
    )
    fake = MagicMock()
    fake.fine_tuning.jobs.retrieve = AsyncMock(return_value=job)
    provider._client = fake
    result = await provider.get_status("ft-1")
    assert result.status is TrainingStatus.VALIDATING


def test_openai_client_is_shared_and_has_timeout() -> None:
    provider = OpenAIProvider(api_key="k")
    with patch("openai.AsyncOpenAI") as ctor:
        first = provider.client()
        second = provider.client()
    assert first is second
    ctor.assert_called_once_with(api_key="k", timeout=PROVIDER_TIMEOUT_S)


async def test_openai_aclose_closes_and_resets() -> None:
    provider = OpenAIProvider(api_key="k")
    fake = MagicMock()
    fake.close = AsyncMock()
    provider._client = fake
    await provider.aclose()
    fake.close.assert_awaited_once()
    assert provider._client is None
    await provider.aclose()  # idempotent


async def test_together_reuses_one_client_and_maps_completed() -> None:
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            200, json={"id": "j", "model": "m", "status": "completed"}
        )

    provider = TogetherProvider(api_key="k")
    provider._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    shared = provider.client()
    first = await provider.get_status("j")
    second = await provider.get_status("j")
    assert provider.client() is shared
    assert first.status is TrainingStatus.SUCCEEDED
    assert second.status is TrainingStatus.SUCCEEDED
    assert len(calls) == 2
    await provider.aclose()
    assert provider._http is None
    assert shared.is_closed
