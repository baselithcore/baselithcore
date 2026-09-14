"""``BASELITH_UNKNOWN_MODEL_COST_POLICY=reject`` must refuse, not destroy.

The setting's own description reads as a pre-call gate — it "raises
UnknownModelCostRejected instead of billing anything". There was no pre-call
pricing check anywhere: the only place the policy could fire was the six
``llm_call_cost_usd`` accounting sites, which run *after* the provider has
answered and outside the try that wraps generation. A ``reject`` deployment
therefore paid for a completed generation and then threw the answer away with
an uncaught exception, while its sibling
(``core.orchestration.budget_context``) had already decided that a policy
rejection is a "don't charge", not a run-aborting error.

Both halves are covered here: the refusal now happens before the spend, and a
turn that reaches accounting with an unpriceable model (the fallback chain can
swap the serving model after the gate) is still delivered.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from core.quotas import cost_enforcement as ce_mod
from core.quotas.cost_enforcement import (
    UnknownModelCostRejected,
    enforce_tenant_cost_budget,
)
from core.services.llm.service import LLMService
from core.services.llm.structured import generate_structured
from core.services.llm.tool_calling import LLMResult
from core.services.llm.usage import Usage

pytestmark = [pytest.mark.unit]

PRICED = "gpt-4o-mini"
UNPRICED = "some-self-hosted-model"


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


@pytest.fixture(autouse=True)
def _isolate_policy(monkeypatch):
    """Reset the policy singleton and the warn-once set around each test."""
    ce_mod._warned_unknown_model_ids.clear()
    monkeypatch.setattr(ce_mod, "_unknown_model_cost_config", None)
    monkeypatch.delenv("BASELITH_UNKNOWN_MODEL_COST_POLICY", raising=False)
    yield
    ce_mod._warned_unknown_model_ids.clear()


@pytest.fixture
def reject_policy(monkeypatch):
    monkeypatch.setenv("BASELITH_UNKNOWN_MODEL_COST_POLICY", "reject")


@pytest.fixture
def booked(monkeypatch):
    recorded: list[float] = []

    async def _record(usd, **_kwargs):
        recorded.append(usd)

    monkeypatch.setattr("core.quotas.cost_enforcement.record_tenant_llm_cost", _record)
    return recorded


def _service(*, native: bool = False) -> LLMService:
    with patch("core.services.llm.service.get_llm_config") as config:
        config.return_value = Mock(
            provider="openai",
            model=PRICED,
            enable_cache=False,
            fallback_chain="",
            max_concurrent_requests=0,
            enable_native_tools=native,
            thinking_enabled=False,
        )
        service = LLMService()
    service.provider = SimpleNamespace(supports_native_tools=native)
    service.cost_tracker = None
    return service


class TestTheRefusalHappensBeforeTheSpend:
    async def test_the_gate_rejects_an_unpriced_model(self, reject_policy):
        with pytest.raises(UnknownModelCostRejected):
            await enforce_tenant_cost_budget(model=UNPRICED)

    async def test_a_priced_model_passes(self, reject_policy):
        await enforce_tenant_cost_budget(model=PRICED)

    async def test_the_other_policies_do_not_reject(self, monkeypatch):
        monkeypatch.setenv("BASELITH_UNKNOWN_MODEL_COST_POLICY", "zero")
        await enforce_tenant_cost_budget(model=UNPRICED)

    async def test_the_provider_is_never_called(self, reject_policy):
        """ "Instead of billing anything" is only true before the call."""
        service = _service(native=True)
        native = AsyncMock(return_value=LLMResult(text="hi", tokens_used=10))

        with patch("core.services.llm.structured._native_with_retry", native):
            with pytest.raises(UnknownModelCostRejected):
                await generate_structured(service, "q", model=UNPRICED)

        assert not native.called


class TestACompletedTurnIsNeverDestroyedByThePolicy:
    async def test_a_stream_served_by_an_unpriced_fallback_still_arrives(
        self, reject_policy, booked
    ):
        """The gate saw a priced model; the chain served an unpriced one.

        Pre-fix this raised out of the accounting line at stream end and the
        consumer lost a stream it had already been charged for.
        """
        service = _service()

        async def _open_stream(_service, _prompt, _model, stream_kwargs):
            stream_kwargs["usage_sink"].append(Usage(input_tokens=5, output_tokens=7))

            async def _chunks():
                yield "half", 12

            return _chunks(), None, "anthropic", UNPRICED

        with patch("core.services.llm._streaming.open_stream", _open_stream):
            chunks = [c async for c in service.generate_response_stream("q")]

        assert chunks == ["half"]
        # Rejected means "don't meter" here: nothing was booked, nothing blew up.
        assert booked == []

    async def test_record_usage_cost_swallows_the_rejection(
        self, reject_policy, booked
    ):
        from core.services.llm._accounting import record_usage_cost

        await record_usage_cost(UNPRICED, Usage(input_tokens=100, output_tokens=50))

        assert booked == []


class TestTheDefaultPolicyIsUnchanged:
    async def test_an_unpriced_model_still_meters_unknown_price(self, booked):
        from core.models.pricing import UNKNOWN_PRICE
        from core.services.llm._accounting import record_usage_cost

        await record_usage_cost(UNPRICED, Usage(input_tokens=100, output_tokens=50))

        assert booked == [pytest.approx(UNKNOWN_PRICE.estimate(100, 50))]

    async def test_the_gate_lets_an_unpriced_model_through(self):
        await enforce_tenant_cost_budget(model=UNPRICED)
