"""The fix-rate benchmark: every case is a real bug, and the runner judges by hidden tests."""

from __future__ import annotations

import pytest

from baselith.plugins.coding_agent import CodingAgent
from baselith.plugins.coding_agent.evals import CASES, run_eval
from baselith.plugins.coding_agent.evals.__main__ import main
from baselith.plugins.coding_agent.evals.runner import run_workspace_eval
from baselith.plugins.coding_agent.evals.workspace_cases import WORKSPACE_CASES

from .coding_agent_fakes import ADD_TESTS, GOOD_ADD, LocalSandbox, ScriptedLLM


def agent(answers) -> CodingAgent:
    a = CodingAgent(max_fix_attempts=2, execution_timeout=60)
    a._llm, a._sandbox = ScriptedLLM(answers), LocalSandbox()
    return a


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
async def test_every_case_is_a_real_bug_its_hidden_tests_catch(case):
    report = await agent([])._run_tests(case.code, case.hidden)
    assert not report.green, f"{case.name}: the hidden tests pass on the buggy code"


async def test_a_case_counts_only_when_the_hidden_tests_pass():
    case = next(c for c in CASES if c.name == "wrong_operator")
    good = {"code": GOOD_ADD, "tests": ADD_TESTS, "explanation": "+", "changes": []}
    report = await run_eval(agent([good]), [case])
    assert report.fix_rate == 1.0 and report.results[0].tests_reproduce_bug is True

    # Passing its own (too weak) tests is not enough.
    weak = {
        "code": "def add(a: int, b: int) -> int:\n    return 5\n",
        "tests": "from solution import add\n\n\ndef test_one():\n    assert add(2, 3) == 5\n",
        "explanation": "constant",
        "changes": [],
    }
    report = await run_eval(agent([weak]), [case])
    assert report.results[0].agent_success and not report.results[0].fixed
    assert "fix rate: 0%" in report.render()


def test_the_cli_refuses_an_unknown_case(capsys):
    assert main(["no_such_case"]) == 2
    assert "known:" in capsys.readouterr().out


@pytest.mark.parametrize("case", WORKSPACE_CASES, ids=[c.name for c in WORKSPACE_CASES])
async def test_every_workspace_case_is_a_real_bug_its_hidden_tests_catch(case):
    ws = case.workspace
    report = await agent([])._run_workspace_tests(ws, ws.files[ws.target], case.hidden)
    assert report.total and not report.green, (
        f"{case.name}: hidden tests pass on the bug"
    )


async def test_a_workspace_case_counts_only_when_its_hidden_tests_pass():
    case = next(c for c in WORKSPACE_CASES if c.name == "sibling_import")
    good = {
        "code": case.workspace.files[case.workspace.target].replace(
            "double(x) + 1", "double(double(x))"
        ),
        "tests": "from pkg.mod import quad\n\n\ndef test_quad():\n    assert quad(1) == 4\n",
        "explanation": "nested",
        "changes": [],
    }
    report = await run_workspace_eval(agent([good]), [case])
    assert report.fix_rate == 1.0


def test_the_cli_knows_workspace_mode(capsys):
    assert main(["--workspace", "no_such_case"]) == 2
    assert "sibling_import" in capsys.readouterr().out
