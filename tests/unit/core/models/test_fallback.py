"""Unit tests for ``core.models.fallback``."""

from __future__ import annotations

import pytest

from core.models.fallback import (
    AllProvidersFailedError,
    FallbackChain,
    Provider,
)


class TestFallbackChain:
    async def test_first_provider_succeeds(self) -> None:
        async def primary() -> str:
            return "ok-primary"

        chain = FallbackChain([Provider(name="primary", call=primary)])
        outcome = await chain.run()
        assert outcome.result == "ok-primary"
        assert outcome.provider == "primary"
        assert len(outcome.attempts) == 1
        assert outcome.attempts[0].succeeded

    async def test_falls_through_on_exception(self) -> None:
        async def boom() -> str:
            raise RuntimeError("primary down")

        async def backup() -> str:
            return "ok-backup"

        chain = FallbackChain(
            [
                Provider(name="primary", call=boom),
                Provider(name="backup", call=backup),
            ]
        )
        outcome = await chain.run()
        assert outcome.result == "ok-backup"
        assert outcome.provider == "backup"
        assert outcome.attempts[0].succeeded is False
        assert "primary down" in (outcome.attempts[0].error or "")
        assert outcome.attempts[1].succeeded

    async def test_skips_providers_with_open_breaker(self) -> None:
        called: list[str] = []

        async def primary() -> str:
            called.append("primary")
            return "p"

        async def backup() -> str:
            called.append("backup")
            return "b"

        chain = FallbackChain(
            [
                Provider(name="primary", call=primary, is_open=lambda: True),
                Provider(name="backup", call=backup, is_open=lambda: False),
            ]
        )
        outcome = await chain.run()
        assert outcome.provider == "backup"
        assert called == ["backup"]
        assert outcome.attempts[0].skipped is True
        assert outcome.attempts[0].error == "circuit_open"

    async def test_all_providers_fail_raises(self) -> None:
        async def boom1() -> str:
            raise ValueError("a")

        async def boom2() -> str:
            raise ValueError("b")

        chain = FallbackChain(
            [
                Provider(name="a", call=boom1),
                Provider(name="b", call=boom2),
            ]
        )
        with pytest.raises(AllProvidersFailedError) as exc:
            await chain.run()
        assert len(exc.value.attempts) == 2
        assert all(not a.succeeded for a in exc.value.attempts)

    async def test_all_breakers_open_raises(self) -> None:
        async def call() -> str:
            return "x"

        chain = FallbackChain(
            [
                Provider(name="a", call=call, is_open=lambda: True),
                Provider(name="b", call=call, is_open=lambda: True),
            ]
        )
        with pytest.raises(AllProvidersFailedError) as exc:
            await chain.run()
        assert all(a.skipped for a in exc.value.attempts)

    async def test_args_and_kwargs_passed_through(self) -> None:
        async def echo(x: int, *, y: int) -> int:
            return x + y

        chain = FallbackChain([Provider(name="p", call=echo)])
        outcome = await chain.run(2, y=3)
        assert outcome.result == 5

    async def test_sync_callable_supported(self) -> None:
        def sync_call() -> str:
            return "sync"

        chain = FallbackChain([Provider(name="p", call=sync_call)])
        outcome = await chain.run()
        assert outcome.result == "sync"

    def test_empty_chain_rejected(self) -> None:
        with pytest.raises(ValueError):
            FallbackChain([])

    def test_duplicate_names_rejected(self) -> None:
        async def x() -> int:
            return 1

        with pytest.raises(ValueError):
            FallbackChain([Provider(name="dup", call=x), Provider(name="dup", call=x)])

    async def test_fatal_exception_reraises_without_fallthrough(self) -> None:
        class Fatal(RuntimeError):
            pass

        secondary_called = False

        async def primary() -> str:
            raise Fatal("budget blown")

        async def secondary() -> str:
            nonlocal secondary_called
            secondary_called = True
            return "ok"

        chain = FallbackChain(
            [Provider(name="p1", call=primary), Provider(name="p2", call=secondary)],
            fatal_exceptions=(Fatal,),
        )
        with pytest.raises(Fatal):
            await chain.run()
        assert secondary_called is False

    async def test_non_fatal_still_falls_through(self) -> None:
        async def primary() -> str:
            raise ValueError("boom")

        async def secondary() -> str:
            return "ok"

        chain = FallbackChain(
            [Provider(name="p1", call=primary), Provider(name="p2", call=secondary)],
            fatal_exceptions=(KeyError,),
        )
        outcome = await chain.run()
        assert outcome.result == "ok"
        assert outcome.provider == "p2"

    async def test_attempt_records_exception_type(self) -> None:
        async def boom() -> str:
            raise KeyError("missing")

        async def ok() -> str:
            return "ok"

        chain = FallbackChain(
            [Provider(name="a", call=boom), Provider(name="b", call=ok)]
        )
        outcome = await chain.run()
        assert "KeyError" in (outcome.attempts[0].error or "")


@pytest.mark.asyncio
class TestStageTimeout:
    """Without a per-stage bound, worst-case chain latency is the SUM of every
    provider SDK timeout (120s each by default). A hung stage must count as a
    failed attempt and fall through, not stall the whole chain."""

    async def test_hung_provider_falls_through_to_next(self) -> None:
        import asyncio

        async def hung() -> str:
            await asyncio.sleep(30)
            return "never"

        async def quick() -> str:
            return "ok"

        chain = FallbackChain(
            [Provider(name="hung", call=hung), Provider(name="quick", call=quick)],
            stage_timeout_seconds=0.05,
        )
        outcome = await asyncio.wait_for(chain.run(), timeout=5)
        assert outcome.result == "ok"
        assert outcome.provider == "quick"
        assert outcome.attempts[0].succeeded is False
        assert "timeout" in (outcome.attempts[0].error or "").lower()

    async def test_per_provider_timeout_overrides_chain_default(self) -> None:
        import asyncio

        async def slowish() -> str:
            await asyncio.sleep(0.1)
            return "slow-but-allowed"

        chain = FallbackChain(
            [Provider(name="slowish", call=slowish, timeout_seconds=5.0)],
            stage_timeout_seconds=0.01,
        )
        outcome = await chain.run()
        assert outcome.result == "slow-but-allowed"

    async def test_no_timeout_keeps_legacy_unbounded_behavior(self) -> None:
        async def ok() -> str:
            return "fine"

        chain = FallbackChain([Provider(name="p", call=ok)])
        assert (await chain.run()).result == "fine"
