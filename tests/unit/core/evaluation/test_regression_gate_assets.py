"""Guard for the checked-in eval corpus (``evals/``).

Every case must have a run behind it — either a replayed scenario or a
hand-written recording, never both — every run must pass its case, and the CLI
gate must exit 0 on the shipped corpus. If a flow legitimately changes, the
run changes in the same commit as the case.

The two sources are not equivalent and the tests say so. A recording under
``evals/runs/`` is an object somebody typed, graded against expectations
somebody typed, so it cannot fail for a change to the agent. A scenario under
``evals/scenarios/`` is the provider's half of a conversation, replayed
through the real loop: the trajectory graded is the one the loop produced.
``test_the_gate_catches_a_real_regression`` is the test that says the
difference is real rather than claimed.
"""

from pathlib import Path

import pytest

from core.evaluation.regression_runner import (
    load_cases,
    load_recorded_runs,
    run_regression,
)
from core.evaluation.replay import load_scenarios

REPO_ROOT = Path(__file__).resolve().parents[4]
CASES_DIR = REPO_ROOT / "evals" / "cases"
RUNS_FILE = REPO_ROOT / "evals" / "runs" / "recorded_runs.json"
SCENARIOS_DIR = REPO_ROOT / "evals" / "scenarios"


def _case_ids() -> set[str]:
    return {c["case_id"] for c in load_cases(CASES_DIR)}


class TestTheCorpusIsWellFormed:
    def test_every_case_has_exactly_one_run(self):
        fixtures = set(load_recorded_runs(RUNS_FILE))
        scenarios = {s.case_id for s in load_scenarios(SCENARIOS_DIR)}

        assert not (fixtures & scenarios), (
            "a case served by both a fixture and a scenario is ambiguous; "
            "delete the fixture — the scenario supersedes it"
        )
        assert fixtures | scenarios == _case_ids()

    def test_the_corpus_is_not_trivially_small(self):
        assert len(load_cases(CASES_DIR)) >= 10

    def test_some_cases_are_replayed_through_the_agent(self):
        """A corpus of pure fixtures is a gate that cannot fail for the code."""
        assert load_scenarios(SCENARIOS_DIR), (
            "no scenarios: every case is a hand-written fixture, so the gate "
            "can only go red for an edited YAML file"
        )


class TestTheShippedCorpusPasses:
    def test_every_fixture_passes_its_case(self):
        fixtures = load_recorded_runs(RUNS_FILE)
        cases = [c for c in load_cases(CASES_DIR) if c["case_id"] in fixtures]
        report = run_regression(cases, fixtures, threshold=1.0)

        failing = [(r.case_id, r.violations) for r in report.results if not r.passed]
        assert report.meets_threshold, f"failing: {failing}"

    def test_cli_gate_exits_zero(self):
        import scripts.run_regression_evals as gate

        assert gate.main([]) == 0


class TestTheGateFails:
    def test_a_violated_case_fails(self, tmp_path):
        import json

        import scripts.run_regression_evals as gate

        (tmp_path / "cases").mkdir()
        (tmp_path / "cases" / "c.yaml").write_text(
            "- case_id: x\n  input: q\n  forbidden_tools: [execute_code]\n",
            encoding="utf-8",
        )
        runs_file = tmp_path / "runs.json"
        runs_file.write_text(
            json.dumps(
                [
                    {
                        "case_id": "x",
                        "output_text": "done",
                        "trajectory": [{"name": "execute_code", "args": {}}],
                        "latency_ms": 10,
                    }
                ]
            ),
            encoding="utf-8",
        )
        (tmp_path / "scenarios").mkdir()

        assert (
            gate.main(
                [
                    "--cases",
                    str(tmp_path / "cases"),
                    "--runs",
                    str(runs_file),
                    "--scenarios",
                    str(tmp_path / "scenarios"),
                ]
            )
            == 1
        )

    @pytest.mark.asyncio
    async def test_the_gate_catches_a_real_regression(self):
        """The claim that replay has teeth, checked rather than asserted.

        Dropping the untrusted-content envelope is a real defect — it is what
        keeps a tool's output from reading as an instruction — and it lives
        entirely inside the loop. No hand-written recording can notice it,
        because no hand-written recording runs the loop. A replayed scenario
        does, and this pins that it fails rather than passing quietly.
        """
        from unittest.mock import patch

        from core.evaluation.replay import ReplayError, replay_scenario

        scenario = next(iter(load_scenarios(SCENARIOS_DIR)))

        # Sanity: it replays cleanly before the regression is injected.
        await replay_scenario(scenario)

        # Patched where the renderer binds it, not where it is defined: the
        # name is imported at module scope, so rebinding the source module
        # would leave the live reference untouched and the test green for the
        # wrong reason.
        with (
            patch(
                "core.reasoning.react_tool_gate.wrap_untrusted",
                side_effect=lambda text, **kwargs: text,
            ),
            pytest.raises(ReplayError, match="envelope"),
        ):
            await replay_scenario(scenario)
