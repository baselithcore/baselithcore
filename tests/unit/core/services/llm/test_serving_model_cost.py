"""Whoever answers is who gets billed — and local inference costs no dollars.

Two defects, one root: the funnel booked every turn under the model it *asked*
for, while the fallback chain is free to have another model answer.

* A fallback answer billed to the primary's model prices, say, a local 7B at
  a hosted model's rate. The tenant ledger and the per-run budget both move on
  spend that never happened.
* A self-hosted model has no pricing row, so it was priced through the
  unknown-model policy — ``UNKNOWN_PRICE``, a deliberately punitive 100 $/M
  that exists to make a *missing vendor row* obvious. Applied to local
  inference it invents money: a single 1k-token turn "costs" 0.20 $, and a run
  with a budget cap aborts on a bill nobody will ever receive.

Local inference is not free — it is GPU seconds and memory — but it is not
denominated in dollars, so it is metered as tokens and priced at zero.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.models.pricing import (
    LOCAL_PRICE,
    UNKNOWN_PRICE,
    get_price,
    is_priced,
    qualified_model_id,
)

pytestmark = [pytest.mark.unit]


class TestPricingRules:
    def test_a_local_model_is_namespaced_by_its_provider(self):
        """A bare tag has no vendor: ``llama3.2`` is free locally and billable
        behind a hosted gateway, so the id has to say which one it was."""
        assert qualified_model_id("ollama", "llama3.2") == "ollama/llama3.2"
        assert qualified_model_id("openai", "gpt-4o-mini") == "gpt-4o-mini"
        assert qualified_model_id(None, "gpt-4o-mini") == "gpt-4o-mini"

    def test_namespacing_is_idempotent(self):
        assert qualified_model_id("ollama", "ollama/llama3.2") == "ollama/llama3.2"

    def test_any_local_model_costs_zero_without_a_table_row(self):
        """The fix: no pricing row is needed, and none can be forgotten."""
        assert get_price("ollama/qwen2.5:7b-instruct") is LOCAL_PRICE
        assert (
            get_price("ollama/whatever-was-pulled-today").estimate(10_000, 10_000)
            == 0.0
        )

    def test_an_unknown_hosted_model_still_charges_unknown_price(self):
        """The warning signal this table exists for must keep working."""
        assert get_price("some-new-vendor-model") is UNKNOWN_PRICE

    def test_an_explicit_row_still_wins_for_a_local_id(self):
        """A local endpoint fronting a paid model can be priced explicitly."""
        table = {"ollama/paid-proxy": UNKNOWN_PRICE}
        assert get_price("ollama/paid-proxy", table=table) is UNKNOWN_PRICE

    def test_is_priced_separates_known_zero_from_unknown(self):
        assert is_priced("ollama/anything") is True
        assert is_priced("gpt-4o-mini") is True
        assert is_priced("mystery-model") is False


def _service(chain: str, provider: str = "openai", model: str = "gpt-4o-mini"):
    from core.config.services import LLMConfig
    from core.services.llm.service import LLMService

    config = LLMConfig(
        provider=provider,
        model=model,
        fallback_chain=chain,
        enable_cache=False,
        api_key="test-key-not-a-real-credential",
    )
    with patch.object(LLMService, "_create_provider", return_value=AsyncMock()):
        return LLMService(config=config, enable_cache=False)


@pytest.fixture(autouse=True)
def _isolated_token_ledger():
    """Keep these token reports out of the process-wide cost context."""
    from core.middleware.cost_control import _cost_context

    token = _cost_context.set(None)
    try:
        yield
    finally:
        _cost_context.reset(token)


@pytest.mark.asyncio
class TestTurnIsBilledToWhoeverAnswered:
    async def _run_with_budget(self, service, clone_result):
        from core.orchestration.budget_context import activate_budget, deactivate_budget
        from core.orchestration.limits import LoopBudget, LoopLimits

        clone = AsyncMock()
        clone._generate_with_retry = AsyncMock(return_value=clone_result)
        budget = LoopBudget(limits=LoopLimits(budget_usd=100.0))
        token = activate_budget(budget)
        try:
            with (
                patch.object(
                    service,
                    "_generate_with_retry",
                    AsyncMock(side_effect=RuntimeError("primary down")),
                ),
                patch(
                    "core.services.llm.fallback_runtime._clone_service",
                    return_value=clone,
                ),
            ):
                await service.generate_response("hello")
        finally:
            deactivate_budget(token)
        return budget

    async def test_a_local_fallback_costs_tokens_not_dollars(self):
        """The GPU is busy; the invoice is not. Both have to be true at once."""
        service = _service("ollama:qwen2.5:7b-instruct")
        budget = await self._run_with_budget(service, ("local answer", 4_000))
        assert budget.cost_usd == 0.0
        # Tokens are the real resource here, and they are still counted — a
        # run cannot escape its cap by moving to a local model.
        assert budget.tokens > 0

    async def test_a_hosted_fallback_is_priced_as_itself(self):
        """Not as the primary: ``gpt-4o-mini`` and ``gpt-4o`` differ ~16x."""
        service = _service("openai:gpt-4o", model="gpt-4o-mini")
        budget = await self._run_with_budget(service, ("saved", 2_000))

        from core.models.pricing import estimate_cost

        # Booked at the FALLBACK's rate. The primary's rate would be ~16x
        # cheaper, i.e. a ledger that quietly understates every failover.
        assert budget.cost_usd > 0
        assert budget.cost_usd != pytest.approx(
            estimate_cost("gpt-4o-mini", 0, 2_000), rel=0.01
        )

    async def test_the_span_records_the_model_that_answered(self):
        service = _service("ollama:qwen2.5:7b-instruct")
        recorded: dict[str, object] = {}

        class _Span:
            def set_attribute(self, key, value):
                recorded[key] = value

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        class _Tracer:
            def start_span(self, *_args, **_kwargs):
                return _Span()

        clone = AsyncMock()
        clone._generate_with_retry = AsyncMock(return_value=("local", 10))
        with (
            patch.object(
                service,
                "_generate_with_retry",
                AsyncMock(side_effect=RuntimeError("primary down")),
            ),
            patch(
                "core.services.llm.fallback_runtime._clone_service", return_value=clone
            ),
            patch("core.observability.get_tracer", return_value=_Tracer()),
        ):
            await service.generate_response("hello")

        assert recorded["gen_ai.baselith.serving_provider"] == "ollama"
        assert recorded["gen_ai.response.model"] == "qwen2.5:7b-instruct"
