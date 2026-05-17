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
