"""Unit tests for ``core.orchestration.budget_context``.

Covers the ambient ``LoopBudget`` charging seam: cache/batch kwarg
passthrough to :func:`core.models.pricing.estimate_cost` for priced models,
and unknown-model handling for unpriced models — routed through
:func:`core.quotas.cost_enforcement.price_unknown_model` so both charging
seams (this one and tenant/identity metering) share one
``BASELITH_UNKNOWN_MODEL_COST_POLICY`` policy (default ``charge``, i.e.
``UNKNOWN_PRICE``). The once-per-model-id warning fires regardless of
whether an ambient budget is active at all.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.orchestration import budget_context as bc_mod
from core.orchestration.budget_context import (
    activate_budget,
    charge_llm_cost,
    deactivate_budget,
)
from core.orchestration.limits import LoopBudget, LoopLimits
from core.quotas import cost_enforcement as ce_mod


@pytest.fixture(autouse=True)
def _reset_unknown_model_state(monkeypatch):
    bc_mod._warned_unpriced_model_ids.clear()
    monkeypatch.setattr(ce_mod, "_unknown_model_cost_config", None)
    ce_mod._warned_unknown_model_ids.clear()
    monkeypatch.delenv("BASELITH_UNKNOWN_MODEL_COST_POLICY", raising=False)
    yield
    bc_mod._warned_unpriced_model_ids.clear()
    ce_mod._warned_unknown_model_ids.clear()


class TestChargeKnownModel:
    def test_threads_cache_and_batch_kwargs(self):
        from core.models.pricing import DEFAULT_PRICING

        model = next(iter(DEFAULT_PRICING))
        price = DEFAULT_PRICING[model]
        budget = LoopBudget(limits=LoopLimits(budget_usd=1000.0))
        token = activate_budget(budget)
        try:
            cost = charge_llm_cost(
                model,
                0,
                0,
                cache_read_tokens=1_000_000,
                cache_write_tokens=0,
                batch=True,
            )
        finally:
            deactivate_budget(token)
        assert cost == pytest.approx(price.effective_cache_read_usd_per_million * 0.5)


class TestUnpricedModelCharging:
    def test_charges_unknown_price_by_default(self):
        """The brief requires UNKNOWN_PRICE charging here too, not just in
        core.quotas.cost_enforcement — default policy is 'charge'."""
        from core.models.pricing import UNKNOWN_PRICE

        budget = LoopBudget(limits=LoopLimits(budget_usd=1000.0, max_tokens=1000))
        token = activate_budget(budget)
        try:
            cost = charge_llm_cost("some-self-hosted-model", 100, 50)
        finally:
            deactivate_budget(token)
        assert cost == pytest.approx(UNKNOWN_PRICE.estimate(100, 50))
        assert budget.cost_usd == pytest.approx(cost)

    def test_zero_policy_charges_nothing(self, monkeypatch):
        monkeypatch.setenv("BASELITH_UNKNOWN_MODEL_COST_POLICY", "zero")
        budget = LoopBudget(limits=LoopLimits(max_tokens=1000))
        token = activate_budget(budget)
        try:
            cost = charge_llm_cost("some-self-hosted-model", 100, 50)
        finally:
            deactivate_budget(token)
        assert cost == 0.0
        assert budget.tokens == 150  # tokens still counted

    def test_reject_policy_is_guarded_and_charges_nothing(self, monkeypatch):
        """UnknownModelCostRejected must not escape charge_llm_cost — a
        BudgetExceededError is this module's only charging exception."""
        monkeypatch.setenv("BASELITH_UNKNOWN_MODEL_COST_POLICY", "reject")
        budget = LoopBudget(limits=LoopLimits(max_tokens=1000))
        token = activate_budget(budget)
        try:
            cost = charge_llm_cost("some-self-hosted-model", 100, 50)
        finally:
            deactivate_budget(token)
        assert cost == 0.0

    def test_tokens_still_enforced_under_charge_policy(self):
        """Token cap enforces even for models absent from the pricing table,
        independent of the (now nonzero) dollar charge."""
        budget = LoopBudget(limits=LoopLimits(budget_usd=1000.0, max_tokens=100))
        token = activate_budget(budget)
        try:
            with pytest.raises(Exception):  # BudgetExceededError, max_tokens
                charge_llm_cost("unlisted-model", 80, 40)
        finally:
            deactivate_budget(token)


class TestUnpricedModelWarning:
    def test_warns_once_per_model_id_per_process(self):
        budget = LoopBudget(limits=LoopLimits(budget_usd=1000.0, max_tokens=100_000))
        token = activate_budget(budget)
        try:
            with patch.object(bc_mod.logger, "warning") as warn:
                charge_llm_cost("repeat-unpriced-model", 10, 10)
                charge_llm_cost("repeat-unpriced-model", 10, 10)
                assert warn.call_count == 1
                charge_llm_cost("another-unpriced-model", 10, 10)
                assert warn.call_count == 2
        finally:
            deactivate_budget(token)

    def test_warning_fires_even_when_no_budget_is_active(self):
        """Visibility into a missing pricing entry must not depend on
        whether an orchestrated request happens to be running."""
        with patch.object(bc_mod.logger, "warning") as warn:
            charge_llm_cost("unpriced-and-no-active-budget", 10, 10)
            warn.assert_called_once()

    def test_no_active_budget_still_charges_nothing(self):
        cost = charge_llm_cost("unpriced-and-no-active-budget-2", 10, 10)
        assert cost == 0.0
