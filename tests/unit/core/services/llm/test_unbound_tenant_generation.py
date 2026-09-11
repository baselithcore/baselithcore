"""Generation must survive an unbound tenant under strict isolation.

The cache key and the response caches namespace their entries by tenant, but
that id is not an access boundary. A strict lookup there used to raise
``TenantContextError`` for every out-of-request caller (a plugin background
task, a scheduler, a CLI script), killing the generation before the provider
was ever called — the failure surfaced only as a red span in the trace view.
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from core.context import _tenant_context
from core.services.llm import LLMService
from core.services.llm._generation import _build_cache_key


@pytest.fixture
def strict_isolation_without_tenant():
    """Strict isolation on, no tenant bound to the current context."""
    token = _tenant_context.set(None)
    with patch("core.context.get_app_config") as mock_config:
        mock_config.return_value.strict_tenant_isolation = True
        yield
    _tenant_context.reset(token)


def test_cache_key_falls_back_to_default_tenant(strict_isolation_without_tenant):
    """An unbound caller gets the shared ``default`` bucket, not an exception."""
    cache_key, prompt_hash = _build_cache_key(
        model="gpt-4o-mini",
        prompt="Radio call",
        json_mode=False,
        system_prompt=None,
        temperature=None,
        max_tokens=None,
        effort=None,
    )

    assert cache_key.startswith("default:gpt-4o-mini:False:")
    assert cache_key.endswith(prompt_hash)


def test_cache_key_still_isolates_a_bound_tenant():
    """A bound tenant keeps its own bucket — the fallback is not a merge."""
    token = _tenant_context.set("tenant-123")
    try:
        cache_key, _ = _build_cache_key(
            model="gpt-4o-mini",
            prompt="Radio call",
            json_mode=False,
            system_prompt=None,
            temperature=None,
            max_tokens=None,
            effort=None,
        )
    finally:
        _tenant_context.reset(token)

    assert cache_key.startswith("tenant-123:")


@pytest.mark.asyncio
@patch("core.services.llm.service.get_llm_config")
async def test_generate_response_without_tenant_context(
    mock_config, strict_isolation_without_tenant
):
    """A background-task generation reaches the provider instead of raising."""
    mock_config.return_value = Mock(
        provider="ollama",
        model="llama3.2",
        api_base=None,
        enable_cache=False,
        cache_max_size=1000,
        cache_ttl=3600,
    )

    service = LLMService()
    mock_provider = Mock()
    mock_provider.generate = AsyncMock(return_value=("Box this lap", 12))
    service.provider = mock_provider
    service._provider_chain = [mock_provider]

    response = await service.generate_response("Phrase this decision")

    assert response == "Box this lap"
    mock_provider.generate.assert_called_once()
