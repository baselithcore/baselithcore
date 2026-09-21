"""A degraded feature must not be an outage, and must not be silent either.

On 2026-09-19 a deployment crash-looped for hours: `llava:7b` had been
removed from the Ollama box the vision service points at, `_run_preflight`
treats every non-connectivity failure as fatal, and `Restart=always` did the
rest. Nothing there used vision — the plugins that call it were all
disabled — so one unused model kept every other plugin offline and the proxy
in front answered 502 the whole time.

The fix has two halves, and the second is what keeps it honest: a local model
only the vision service asks for is a warning rather than a blocker, and a
warning that blocks nothing still has to be printed, or the next operator
learns about it the same way.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from core.cli.commands.doctor_checks import CheckResult
from core.services.llm.preflight import PreflightFinding


@pytest.fixture(autouse=True)
def _isolated_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Keep `ensure_data_dirs` inside tmp, away from the repository's own."""
    monkeypatch.chdir(tmp_path)
    data_dir = tmp_path / "data"
    monkeypatch.setenv("CORE_DATA_DIR", str(data_dir))
    return data_dir


def _with_checks(monkeypatch: pytest.MonkeyPatch, *checks: CheckResult) -> None:
    import core.cli.commands.doctor as doctor

    monkeypatch.setattr(doctor, "run_checks", lambda **_kwargs: list(checks))


class TestStartupGate:
    def test_a_warning_check_does_not_block_startup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from core.cli.commands.run import _run_preflight

        _with_checks(
            monkeypatch,
            CheckResult(
                "LLM Local Models",
                False,
                "model 'llava:7b' is not installed — image understanding only",
                "Run: ollama pull llava:7b",
                "warn",
            ),
        )

        assert _run_preflight() == 0

    def test_a_warning_check_is_still_printed(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Booting past it is fine; hiding it is how it bites the next person."""
        from core.cli.commands.run import _run_preflight

        _with_checks(
            monkeypatch,
            CheckResult(
                "LLM Local Models",
                False,
                "model 'llava:7b' is not installed",
                "Run: ollama pull llava:7b",
                "warn",
            ),
        )

        _run_preflight()

        printed = capsys.readouterr().out
        assert "LLM Local Models" in printed
        assert "warning" in printed.lower()

    def test_a_blocking_check_still_stops_startup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from core.cli.commands.run import _run_preflight

        _with_checks(
            monkeypatch,
            CheckResult(
                "LLM Local Models",
                False,
                "model 'llama3.2' is not installed",
                "Run: ollama pull llama3.2",
                "fail",
            ),
        )

        assert _run_preflight() == 1

    def test_a_passing_check_prints_no_warning_panel(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from core.cli.commands.run import _run_preflight

        _with_checks(
            monkeypatch,
            CheckResult("LLM Local Models", True, "All local targets reachable"),
        )

        assert _run_preflight() == 0
        assert "Startup preflight warnings" not in capsys.readouterr().out


class TestLocalModelsCheckSeverity:
    def test_vision_only_findings_are_a_warning(self) -> None:
        from core.cli.commands.doctor_llm import check_llm_local_endpoints

        finding = PreflightFinding(
            severity="warning",
            code="ollama_vision_model_missing",
            message="model 'llava:7b' is not installed — image understanding only",
            remedy="Run: ollama pull llava:7b",
        )
        with patch(
            "core.services.llm.preflight.check_local_endpoints",
            AsyncMock(return_value=[finding]),
        ):
            result = check_llm_local_endpoints()

        assert not result.passed
        assert result.severity == "warn"

    def test_a_blocking_finding_headlines_and_fails(self) -> None:
        """The gap that cannot be served around is the one to read first."""
        from core.cli.commands.doctor_llm import check_llm_local_endpoints

        vision = PreflightFinding(
            severity="warning",
            code="ollama_vision_model_missing",
            message="model 'llava:7b' is not installed",
            remedy="Run: ollama pull llava:7b",
        )
        primary = PreflightFinding(
            severity="error",
            code="ollama_model_missing",
            message="model 'llama3.2' is not installed",
            remedy="Run: ollama pull llama3.2",
        )
        with patch(
            "core.services.llm.preflight.check_local_endpoints",
            AsyncMock(return_value=[vision, primary]),
        ):
            result = check_llm_local_endpoints()

        assert result.severity == "fail"
        assert "llama3.2" in result.message

    def test_no_findings_still_passes(self) -> None:
        from core.cli.commands.doctor_llm import check_llm_local_endpoints

        with patch(
            "core.services.llm.preflight.check_local_endpoints",
            AsyncMock(return_value=[]),
        ):
            result = check_llm_local_endpoints()

        assert result.passed
        assert result.severity == "fail"  # the default; nothing to report
