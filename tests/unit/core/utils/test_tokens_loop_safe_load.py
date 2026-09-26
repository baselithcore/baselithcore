"""The tiktoken encoder is never loaded on the event loop thread.

Its first load reads (or downloads) the BPE ranks file. It used to happen
inline on the first ``estimate_tokens`` call, which from async code meant on
the event loop.
"""

from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

import core.utils.tokens as tokens_module


@pytest.fixture
def cold_encoder(monkeypatch):
    """Reset the cached encoder and install a slow, thread-recording loader."""
    monkeypatch.setattr(tokens_module, "_encoder", None)
    monkeypatch.setattr(tokens_module, "_tiktoken_available", None)
    monkeypatch.setattr(tokens_module, "_encoder_warmup_started", False)
    load_threads: list[int] = []
    encoder = MagicMock()
    encoder.encode.return_value = [1, 2, 3]

    def slow_load(_model):
        load_threads.append(threading.get_ident())
        time.sleep(0.05)
        return encoder

    fake_tiktoken = MagicMock()
    fake_tiktoken.encoding_for_model.side_effect = slow_load
    with patch.dict("sys.modules", {"tiktoken": fake_tiktoken}):
        yield load_threads


async def test_sync_estimate_on_the_loop_does_not_load_inline(cold_encoder):
    loop_thread = threading.get_ident()

    assert tokens_module.estimate_tokens("hello world") >= 1  # heuristic
    for _ in range(100):
        if tokens_module._tiktoken_available is not None:
            break
        await asyncio.sleep(0.01)

    assert cold_encoder and loop_thread not in cold_encoder
    assert tokens_module.estimate_tokens("hello world") == 3  # now exact


async def test_async_estimate_loads_off_loop_and_is_exact(cold_encoder):
    loop_thread = threading.get_ident()

    assert await tokens_module.estimate_tokens_async("hello world") == 3
    assert cold_encoder and loop_thread not in cold_encoder


def test_sync_caller_without_a_loop_loads_inline(cold_encoder):
    assert tokens_module.estimate_tokens("hello world") == 3
    assert cold_encoder == [threading.get_ident()]
