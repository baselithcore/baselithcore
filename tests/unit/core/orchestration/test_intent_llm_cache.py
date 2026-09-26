"""A failed LLM classification must not be memoized.

``_classify_with_llm`` returns None on a timeout, provider error or unparseable
reply. Caching that None pinned the input to the default intent until the LRU
evicted it, even after the provider recovered.
"""

from unittest.mock import AsyncMock

import pytest

from core.orchestration.intent_classifier import ClassificationResult, IntentClassifier


@pytest.mark.asyncio
async def test_none_result_is_not_cached():
    classifier = IntentClassifier()
    good = ClassificationResult(intent="weather", confidence=0.9, method="llm")
    classifier._classify_with_llm = AsyncMock(side_effect=[None, good])  # type: ignore[method-assign]

    assert await classifier._classify_with_llm_cached("rain tomorrow?") is None
    assert await classifier._classify_with_llm_cached("rain tomorrow?") is good
    assert classifier._classify_with_llm.await_count == 2


@pytest.mark.asyncio
async def test_successful_result_is_cached():
    classifier = IntentClassifier()
    good = ClassificationResult(intent="weather", confidence=0.9, method="llm")
    classifier._classify_with_llm = AsyncMock(return_value=good)  # type: ignore[method-assign]

    await classifier._classify_with_llm_cached("rain tomorrow?")
    assert await classifier._classify_with_llm_cached("rain tomorrow?") is good
    assert classifier._classify_with_llm.await_count == 1
