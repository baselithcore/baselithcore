"""Tests for the token-report observer seam (`register_token_sink`).

The seam exists because monkeypatching ``service._report_tokens_to_middleware``
does not intercept the real call path: every generation module imports
``report_tokens_to_middleware`` directly from ``_telemetry`` at import time.
These tests pin the observer contract from the direct-import reference each
call site actually holds.
"""

from __future__ import annotations

import pytest

from core.services.llm._telemetry import (
    register_token_sink,
    unregister_token_sink,
)


def test_sink_fires_from_directly_imported_reference() -> None:
    # Import the way a generation module does: a direct reference bound at
    # import time. The observer must still fire — this is the regression that
    # silently detached the cost ledger when the seam was a monkeypatch.
    from core.services.llm._generation import report_tokens_to_middleware as sink_ref

    seen: list[tuple[int, str]] = []

    def sink(count: int, model: str) -> None:
        seen.append((count, model))

    register_token_sink(sink)
    try:
        sink_ref(120, "input")
        sink_ref(30, "test-model")
    finally:
        unregister_token_sink(sink)

    assert seen == [(120, "input"), (30, "test-model")]


def test_zero_and_negative_counts_are_not_reported() -> None:
    from core.services.llm._telemetry import report_tokens_to_middleware

    seen: list[tuple[int, str]] = []

    def sink(count: int, model: str) -> None:
        seen.append((count, model))

    register_token_sink(sink)
    try:
        report_tokens_to_middleware(0, "m")
        report_tokens_to_middleware(-5, "m")
    finally:
        unregister_token_sink(sink)
    assert seen == []


def test_register_is_idempotent_and_unregister_removes() -> None:
    from core.services.llm._telemetry import report_tokens_to_middleware

    seen: list[int] = []

    def sink(count: int, model: str) -> None:
        seen.append(count)

    register_token_sink(sink)
    register_token_sink(sink)  # duplicate registration must not double-fire
    try:
        report_tokens_to_middleware(7, "m")
    finally:
        unregister_token_sink(sink)
    assert seen == [7]

    report_tokens_to_middleware(9, "m")  # after unregister: silent
    assert seen == [7]
    unregister_token_sink(sink)  # absent → no-op, no raise


def test_sink_exception_never_breaks_the_report() -> None:
    from core.services.llm._telemetry import report_tokens_to_middleware

    def broken(count: int, model: str) -> None:
        raise RuntimeError("observer bug")

    register_token_sink(broken)
    try:
        report_tokens_to_middleware(5, "m")  # must not raise
    finally:
        unregister_token_sink(broken)


def test_sink_fires_even_when_budget_check_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Consumed tokens must be accounted even when the middleware budget gate
    # rejects the request.
    import core.services.llm._telemetry as telemetry

    class _Boom:
        def track_tokens(self, count: int, model: str = "unknown") -> None:
            raise RuntimeError("budget exceeded")

    monkeypatch.setattr(telemetry, "cost_controller", _Boom())

    seen: list[int] = []

    def sink(count: int, model: str) -> None:
        seen.append(count)

    register_token_sink(sink)
    try:
        with pytest.raises(RuntimeError, match="budget exceeded"):
            telemetry.report_tokens_to_middleware(11, "m")
    finally:
        unregister_token_sink(sink)
    assert seen == [11]


class TestReportExternalUsage:
    """``report_external_usage``: the seam for engines outside the funnel.

    Plugins that drive their own LLM client (a vendored engine, a child
    process) measure usage themselves; without this they reported nothing and
    every consumer of the seam — per-plugin cost attribution, per-user budget
    accounting — showed them as having spent zero.
    """

    def test_emits_the_paired_input_then_model_reports(self) -> None:
        from core.services.llm import report_external_usage

        seen: list[tuple[int, str]] = []

        def sink(count: int, model: str) -> None:
            seen.append((count, model))

        register_token_sink(sink)
        try:
            report_external_usage("qwen3:8b", prompt_tokens=900, completion_tokens=120)
        finally:
            unregister_token_sink(sink)

        # Order is load-bearing: consumers pair an ``input`` report with the
        # next real-model report to reconstruct one call.
        assert seen == [(900, "input"), (120, "qwen3:8b")]

    def test_streaming_uses_the_input_stream_sentinel(self) -> None:
        from core.services.llm import report_external_usage

        seen: list[tuple[int, str]] = []

        def sink(count: int, model: str) -> None:
            seen.append((count, model))

        register_token_sink(sink)
        try:
            report_external_usage(
                "gpt-4o-mini", prompt_tokens=10, completion_tokens=2, stream=True
            )
        finally:
            unregister_token_sink(sink)

        assert seen == [(10, "input_stream"), (2, "gpt-4o-mini")]

    def test_skips_missing_counts(self) -> None:
        from core.services.llm import report_external_usage

        seen: list[tuple[int, str]] = []

        def sink(count: int, model: str) -> None:
            seen.append((count, model))

        register_token_sink(sink)
        try:
            report_external_usage("m", prompt_tokens=0, completion_tokens=5)
            report_external_usage("m", prompt_tokens=-3, completion_tokens=0)
        finally:
            unregister_token_sink(sink)

        assert seen == [(5, "m")]

    def test_budget_rejection_never_reaches_the_caller(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The tokens were already spent by an engine this process does not
        # gate, so a budget raise must not corrupt a paid-for response — and
        # must not swallow the second half of the pair either.
        import core.services.llm._telemetry as telemetry

        class _Boom:
            def track_tokens(self, count: int, model: str = "unknown") -> None:
                raise RuntimeError("budget exceeded")

        monkeypatch.setattr(telemetry, "cost_controller", _Boom())

        seen: list[tuple[int, str]] = []

        def sink(count: int, model: str) -> None:
            seen.append((count, model))

        register_token_sink(sink)
        try:
            telemetry.report_external_usage("m", prompt_tokens=4, completion_tokens=6)
        finally:
            unregister_token_sink(sink)

        assert seen == [(4, "input"), (6, "m")]
