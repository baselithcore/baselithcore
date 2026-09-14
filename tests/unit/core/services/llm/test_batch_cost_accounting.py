"""A batch job has to be metered, and metered at the batch rate.

The Message Batches API bills 50% of standard token prices. The pricing table
has carried ``batch_multiplier`` all along and ``estimate_cost``,
``llm_call_cost_usd`` and ``charge_llm_cost`` all take ``batch=``, but nothing
passed it — and on inspection the Anthropic batch path did not reach an
accounting site *at all*. It called ``client.messages.batches`` directly: no
pre-call gate, no tenant ledger, no metrics. Batch spend was not mispriced at
2x; it was invisible, and a tenant over its cost cap could still submit an
unbounded job.

The sequential fallback is the opposite case and must stay as it is: it runs
ordinary ``generate_response`` calls at the full interactive rate, buys no
discount, and would *under*-meter by 2x if someone "fixed" it with
``batch=True``. The last test here guards that.

Concrete figures, ``claude-sonnet-5`` ($2/$10 per 1M), one entry of
1000 input + 1000 output tokens: $0.012 interactive, $0.006 batched.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from core.config.quotas import QuotaConfig
from core.context import set_tenant_context
from core.quotas.manager import CostBudgetExceededError, QuotaManager
from core.quotas.store import InMemoryQuotaStore
from core.services.llm.batch import BatchPrompt, generate_batch
from core.services.llm.service import LLMService

pytestmark = [pytest.mark.unit]

MODEL = "claude-sonnet-5"
ENTRY_INTERACTIVE_USD = 0.012
ENTRY_BATCH_USD = 0.006


@pytest.fixture(autouse=True)
def _isolated_token_ledger():
    """Keep this module's token reports out of the process-wide cost context.

    ``cost_controller`` counts tokens in a ContextVar that is initialized —
    and never restored — by ``tests/unit/core/middleware/test_cost_control``,
    so anything reported here would accumulate for the rest of the session
    and trip its 10 000-token agent cap in unrelated tests.
    """
    from core.middleware.cost_control import _cost_context

    token = _cost_context.set(None)
    try:
        yield
    finally:
        _cost_context.reset(token)


@pytest.fixture
def booked(monkeypatch):
    """Capture every USD figure booked on the tenant's ledger."""
    recorded: list[float] = []

    async def _record(usd, **_kwargs):
        recorded.append(usd)

    monkeypatch.setattr("core.quotas.cost_enforcement.record_tenant_llm_cost", _record)
    return recorded


class FakeBatchesAPI:
    """The two SDK calls the batch path makes, plus the results iterator."""

    def __init__(self, results):
        self._results = results
        self.created_requests = None

    async def create(self, requests):
        self.created_requests = requests
        return SimpleNamespace(id="batch-1", processing_status="ended")

    async def retrieve(self, batch_id):  # pragma: no cover - never polled here
        return SimpleNamespace(id=batch_id, processing_status="ended")

    async def results(self, batch_id):
        for item in self._results:
            yield item


class FakeAnthropicService:
    def __init__(self, batches):
        client = SimpleNamespace(messages=SimpleNamespace(batches=batches))
        self.provider = SimpleNamespace(_ensure_client=lambda: client)
        self.config = SimpleNamespace(provider="anthropic", model=MODEL)

    def _resolve_model(self, model):
        return model or self.config.model


def _succeeded(custom_id, *, input_tokens=1000, output_tokens=1000, metered=True):
    usage = (
        SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)
        if metered
        else None
    )
    message = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="ok")], usage=usage
    )
    return SimpleNamespace(
        custom_id=custom_id,
        result=SimpleNamespace(type="succeeded", message=message),
    )


def _errored(custom_id):
    return SimpleNamespace(custom_id=custom_id, result=SimpleNamespace(type="errored"))


class TestTheInvoiceMatchesTheLedger:
    async def test_one_entry_books_half_the_interactive_price(self, booked):
        from core.quotas.cost_enforcement import llm_call_cost_usd

        service = FakeAnthropicService(FakeBatchesAPI([_succeeded("a")]))

        out = await generate_batch(service, [BatchPrompt("a", "A?")], model=MODEL)

        assert out[0].succeeded
        # The interactive price of the same tokens, for contrast...
        assert llm_call_cost_usd(MODEL, 1000, 1000) == pytest.approx(
            ENTRY_INTERACTIVE_USD
        )
        # ...and what the batch actually cost.
        assert booked == [pytest.approx(ENTRY_BATCH_USD)]

    async def test_the_job_is_booked_once_not_once_per_entry(self, booked):
        """A ten-thousand-entry batch must not be ten thousand store writes."""
        results = [_succeeded("a"), _succeeded("b"), _succeeded("c")]
        service = FakeAnthropicService(FakeBatchesAPI(results))
        prompts = [BatchPrompt(i, f"{i}?") for i in ("a", "b", "c")]

        await generate_batch(service, prompts, model=MODEL)

        assert booked == [pytest.approx(3 * ENTRY_BATCH_USD)]

    async def test_failed_entries_cost_nothing(self, booked):
        service = FakeAnthropicService(FakeBatchesAPI([_succeeded("a"), _errored("b")]))
        prompts = [BatchPrompt("a", "A?"), BatchPrompt("b", "B?")]

        out = await generate_batch(service, prompts, model=MODEL)

        assert [c.succeeded for c in out] == [True, False]
        assert booked == [pytest.approx(ENTRY_BATCH_USD)]

    async def test_a_job_the_provider_did_not_meter_books_nothing(self, booked):
        service = FakeAnthropicService(FakeBatchesAPI([_succeeded("a", metered=False)]))

        await generate_batch(service, [BatchPrompt("a", "A?")], model=MODEL)

        assert booked == []


class TestTheJobIsGatedBeforeItIsSubmitted:
    async def test_an_over_budget_tenant_cannot_submit(self, monkeypatch):
        manager = QuotaManager(
            config=QuotaConfig(
                QUOTAS_ENABLED=True,
                QUOTA_BACKEND="memory",
                QUOTA_TENANT_DAILY_COST_USD=1.00,
            ),
            store=InMemoryQuotaStore(),
        )
        set_tenant_context("acme")
        await manager.record_tenant_cost("acme", 1.50)
        monkeypatch.setattr(
            "core.quotas.cost_enforcement.get_quota_manager", lambda: manager
        )
        batches = FakeBatchesAPI([_succeeded("a")])
        service = FakeAnthropicService(batches)

        with pytest.raises(CostBudgetExceededError):
            await generate_batch(service, [BatchPrompt("a", "A?")], model=MODEL)

        assert batches.created_requests is None


class TestTheSequentialFallbackKeepsTheFullRate:
    async def test_it_bills_the_interactive_price(self, booked):
        """It buys no discount, so ``batch=True`` here would halve a real bill."""
        with patch("core.services.llm.service.get_llm_config") as config:
            config.return_value = Mock(
                provider="openai",
                model=MODEL,
                enable_cache=False,
                fallback_chain="",
                max_concurrent_requests=0,
                enable_native_tools=False,
                thinking_enabled=False,
            )
            service = LLMService()
        service.provider = SimpleNamespace()
        service.cost_tracker = None

        async def _generate(**kwargs):
            from core.services.llm.usage import Usage

            kwargs["usage_sink"].append(Usage(input_tokens=1000, output_tokens=1000))
            return "hi", 2000

        service._generate_with_retry = _generate  # type: ignore[method-assign]

        out = await generate_batch(service, [BatchPrompt("a", "A?")], model=MODEL)

        assert out[0].text == "hi"
        assert booked == [pytest.approx(ENTRY_INTERACTIVE_USD)]
