"""close_llm_services closes every cached service exactly once."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from core.services.llm import _fallback_support, runtime


def _svc() -> MagicMock:
    svc = MagicMock()
    svc.close = AsyncMock()
    return svc


async def test_closes_default_policy_and_fallback_services(monkeypatch) -> None:
    default, clone, stage = _svc(), _svc(), _svc()
    monkeypatch.setattr(runtime, "_default_service", default)
    monkeypatch.setattr(runtime, "_policy_services", {("p", "m"): clone})
    monkeypatch.setattr(
        _fallback_support,
        "_fallback_services",
        {("x", "y"): stage, ("z", "w"): default},
    )

    await runtime.close_llm_services()

    default.close.assert_awaited_once()
    clone.close.assert_awaited_once()
    stage.close.assert_awaited_once()
    assert runtime._default_service is None
    assert runtime._policy_services == {}
    assert _fallback_support._fallback_services == {}


async def test_a_failing_close_does_not_stop_the_rest(monkeypatch) -> None:
    bad, good = _svc(), _svc()
    bad.close.side_effect = RuntimeError("boom")
    monkeypatch.setattr(runtime, "_default_service", bad)
    monkeypatch.setattr(runtime, "_policy_services", {("p", "m"): good})
    monkeypatch.setattr(_fallback_support, "_fallback_services", {})

    await runtime.close_llm_services()

    good.close.assert_awaited_once()
