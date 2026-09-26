"""The semantic LLM cache must not cross generation configurations.

It matched on prompt similarity alone, so the same prompt sent with a
different system prompt (or model, or sampling config) was answered from
another configuration's cached completion.
"""

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import numpy as np
import pytest

from core.cache.semantic_cache import SemanticLLMCache
from core.services.llm import LLMService


@pytest.fixture
def semantic_cache():
    with patch("core.nlp.models.get_embedder") as mock_get:
        embedder = MagicMock()
        embedder.encode.return_value = np.array([1.0, 0.0, 0.0])
        mock_get.return_value = embedder
        yield SemanticLLMCache(maxsize=10, ttl=60, threshold=0.8)


@pytest.mark.asyncio
async def test_namespace_partitions_similarity_lookups(semantic_cache):
    await semantic_cache.set("What is Python?", "answer-a", namespace="a")

    assert await semantic_cache.get_similar("What is Python?", namespace="a") == (
        "answer-a"
    )
    assert await semantic_cache.get_similar("What is Python?", namespace="b") is None


@pytest.mark.asyncio
@patch("core.services.llm.service.get_llm_config")
async def test_system_prompt_change_misses_semantic_cache(mock_config, semantic_cache):
    mock_config.return_value = Mock(
        provider="ollama",
        model="llama3.2",
        api_base=None,
        enable_cache=False,
        cache_max_size=1000,
        cache_ttl=3600,
    )
    service = LLMService()
    service.cache = None
    service.semantic_cache = semantic_cache
    provider = Mock()
    provider.generate = AsyncMock(side_effect=[("pirate", 5), ("formal", 5)])
    service.provider = provider
    service._provider_chain = [provider]

    first = await service.generate_response("Greet me", system_prompt="Be a pirate")
    second = await service.generate_response("Greet me", system_prompt="Be formal")

    assert (first, second) == ("pirate", "formal")
    assert provider.generate.await_count == 2
    # Same configuration again: now served from the semantic cache.
    assert await service.generate_response("Greet me", system_prompt="Be formal") == (
        "formal"
    )
    assert provider.generate.await_count == 2
