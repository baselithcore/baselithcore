"""Batch generation, gen_ai semconv metrics, and XFetch early refresh."""

import math
from types import SimpleNamespace

import pytest

from core.services.llm.batch import BatchCompletion, BatchPrompt, generate_batch

# ---------------------------------------------------------------------------
# generate_batch — Anthropic path (fully mocked SDK)
# ---------------------------------------------------------------------------


class FakeBatchesAPI:
    def __init__(self, results_by_id, statuses=("in_progress", "ended")):
        self._results = results_by_id
        self._statuses = list(statuses)
        self.created_requests = None

    async def create(self, requests):
        self.created_requests = requests
        return SimpleNamespace(id="batch-1", processing_status=self._statuses.pop(0))

    async def retrieve(self, batch_id):
        return SimpleNamespace(id=batch_id, processing_status=self._statuses.pop(0))

    async def results(self, batch_id):
        for item in self._results:
            yield item


def _success(custom_id, text):
    block = SimpleNamespace(type="text", text=text)
    message = SimpleNamespace(content=[block])
    return SimpleNamespace(
        custom_id=custom_id,
        result=SimpleNamespace(type="succeeded", message=message),
    )


def _errored(custom_id):
    return SimpleNamespace(custom_id=custom_id, result=SimpleNamespace(type="errored"))


class FakeAnthropicService:
    def __init__(self, batches):
        client = SimpleNamespace(messages=SimpleNamespace(batches=batches))
        self.provider = SimpleNamespace(_ensure_client=lambda: client)
        self.config = SimpleNamespace(provider="anthropic", model="claude-opus-4-8")

    def _resolve_model(self, model):
        return model or self.config.model


async def test_anthropic_batch_end_to_end():
    # Results arrive OUT of submission order — output must be re-ordered.
    batches = FakeBatchesAPI([_success("b", "beta"), _success("a", "alpha")])
    service = FakeAnthropicService(batches)
    prompts = [
        BatchPrompt(custom_id="a", prompt="A?", system_prompt="sys"),
        BatchPrompt(custom_id="b", prompt="B?"),
    ]

    out = await generate_batch(service, prompts, poll_seconds=0.01)

    assert [c.custom_id for c in out] == ["a", "b"]  # submission order
    assert [c.text for c in out] == ["alpha", "beta"]
    assert all(c.succeeded for c in out)
    # System prompt forwarded; messages shaped for the Batches API.
    assert batches.created_requests[0]["params"]["system"] == "sys"
    assert "system" not in batches.created_requests[1]["params"]


async def test_anthropic_batch_error_entries_marked_failed():
    batches = FakeBatchesAPI([_success("a", "ok"), _errored("b")])
    service = FakeAnthropicService(batches)
    out = await generate_batch(
        service,
        [BatchPrompt("a", "A?"), BatchPrompt("b", "B?")],
        poll_seconds=0.01,
    )
    assert out[0].succeeded and out[0].text == "ok"
    assert not out[1].succeeded and out[1].error == "errored"


async def test_batch_timeout():
    batches = FakeBatchesAPI([], statuses=["in_progress"] * 50)
    service = FakeAnthropicService(batches)
    with pytest.raises(TimeoutError):
        await generate_batch(
            service,
            [BatchPrompt("a", "A?")],
            poll_seconds=0.01,
            timeout_seconds=0.05,
        )


async def test_duplicate_custom_ids_rejected():
    service = FakeAnthropicService(FakeBatchesAPI([]))
    with pytest.raises(ValueError):
        await generate_batch(service, [BatchPrompt("x", "1"), BatchPrompt("x", "2")])


async def test_non_anthropic_provider_falls_back_sequentially():
    calls = []

    class SeqService:
        config = SimpleNamespace(provider="ollama", model="llama")
        provider = SimpleNamespace()

        def _resolve_model(self, model):
            return model or "llama"

        async def generate_response(self, prompt, **kwargs):
            calls.append(prompt)
            if prompt == "boom":
                raise RuntimeError("provider down")
            return f"echo:{prompt}"

    out = await generate_batch(
        SeqService(), [BatchPrompt("a", "hi"), BatchPrompt("b", "boom")]
    )
    assert calls == ["hi", "boom"]
    assert out[0] == BatchCompletion("a", "echo:hi", True)
    assert not out[1].succeeded and "provider down" in out[1].error


async def test_empty_batch_short_circuits():
    assert await generate_batch(FakeAnthropicService(FakeBatchesAPI([])), []) == []


# ---------------------------------------------------------------------------
# gen_ai semconv metrics
# ---------------------------------------------------------------------------


def test_record_genai_metrics_emits_histograms():
    from prometheus_client import REGISTRY

    from core.services.llm._telemetry import record_genai_metrics

    record_genai_metrics(
        "anthropic",
        "claude-opus-4-8",
        input_tokens=100,
        output_tokens=50,
        duration_seconds=1.5,
    )

    labels = {
        "gen_ai_system": "anthropic",
        "gen_ai_request_model": "claude-opus-4-8",
    }
    tokens_in = REGISTRY.get_sample_value(
        "gen_ai_client_token_usage_sum", {**labels, "gen_ai_token_type": "input"}
    )
    tokens_out = REGISTRY.get_sample_value(
        "gen_ai_client_token_usage_sum", {**labels, "gen_ai_token_type": "output"}
    )
    duration = REGISTRY.get_sample_value(
        "gen_ai_client_operation_duration_seconds_sum",
        {**labels, "gen_ai_operation_name": "chat"},
    )
    assert tokens_in and tokens_in >= 100
    assert tokens_out and tokens_out >= 50
    assert duration and duration >= 1.5


# ---------------------------------------------------------------------------
# XFetch early refresh
# ---------------------------------------------------------------------------


def _xfetch_cache(beta, ttl=100):
    from core.cache.redis_cache import RedisTTLCache

    cache = object.__new__(RedisTTLCache)  # skip __init__ (needs a client)
    cache._ttl = ttl
    cache._xfetch_beta = beta
    cache._xfetch_delta_seconds = max(ttl * 0.01, 1.0)
    return cache


def test_xfetch_disabled_never_expires_early():
    cache = _xfetch_cache(beta=0.0)
    assert cache._xfetch_expired(1) is False  # even 1ms left: no early miss


def test_xfetch_fresh_entry_survives():
    cache = _xfetch_cache(beta=1.0, ttl=100)
    # 90s remaining vs delta=1s: -beta*delta*ln(rand) is ~ a few seconds max.
    assert cache._xfetch_expired(90_000) is False


def test_xfetch_near_expiry_probabilistic(monkeypatch):
    import random as random_module

    cache = _xfetch_cache(beta=1.0, ttl=100)
    # Force the draw: remaining 0.5s, threshold = -1*1*ln(draw).
    monkeypatch.setattr(random_module, "random", lambda: math.exp(-2.0))  # thr=2s
    assert cache._xfetch_expired(500) is True  # 0.5s < 2s → early refresh
    monkeypatch.setattr(random_module, "random", lambda: math.exp(-0.1))  # thr=0.1s
    assert cache._xfetch_expired(500) is False  # 0.5s > 0.1s → serve hit


def test_xfetch_missing_or_persistent_ttl_never_early():
    cache = _xfetch_cache(beta=1.0)
    assert cache._xfetch_expired(-1) is False  # persistent key
    assert cache._xfetch_expired(None) is False


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
