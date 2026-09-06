"""Fixtures for golden trajectory tests.

``golden_llm(name)`` returns a :class:`RecordedLLMService` replaying
``cassettes/<name>.json`` and verifies at teardown that every recorded turn
was played. Set ``BASELITH_GOLDEN_RECORD=1`` (with provider credentials
configured) to run against the live ``LLMService`` instead and write a fresh
cassette — the author then curates ``expect.prompt_contains`` by hand.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator

import pytest

from tests.golden.cassette import Cassette, RecordedLLMService, RecordingLLMService

RECORD_ENV = "BASELITH_GOLDEN_RECORD"


@pytest.fixture
def golden_llm() -> Iterator[Callable[[str], RecordedLLMService | RecordingLLMService]]:
    recording = os.environ.get(RECORD_ENV, "").strip() in {"1", "true", "yes"}
    opened: list[RecordedLLMService | RecordingLLMService] = []

    def _open(name: str) -> RecordedLLMService | RecordingLLMService:
        service: RecordedLLMService | RecordingLLMService
        if recording:
            from core.services.llm import get_llm_service

            service = RecordingLLMService(get_llm_service(), name=name)
        else:
            service = RecordedLLMService(Cassette.load(name))
        opened.append(service)
        return service

    yield _open

    for service in opened:
        if isinstance(service, RecordingLLMService):
            service.save()
        else:
            service.assert_exhausted()
