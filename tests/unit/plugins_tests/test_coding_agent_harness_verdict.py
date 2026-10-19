"""A green run counts only when the tests demonstrably checked something.

The report must add up (every collected test accounted for, at least one passed) and every
passing test must have executed an assertion; a first test file that checks nothing is never
frozen.
"""

from __future__ import annotations

import pytest

from baselith.plugins.coding_agent import CodingAgent, Workspace
from baselith.plugins.coding_agent.runner.flows import NO_CHECKS, checks_something
from baselith.plugins.coding_agent.runner.harness import (
    build_test_script,
    parse_report,
    report_marker,
)
from baselith.plugins.coding_agent.workspace.harness import (
    build_workspace_script,
    stage,
)

from .coding_agent_fakes import ADD_TESTS, BAD_ADD, GOOD_ADD, LocalSandbox, ScriptedLLM

KEY = "0123456789abcdef0123456789abcdef"  # gitleaks:allow (fake fixture)
NO_ASSERT_TESTS = (
    "from solution import add\n\n\ndef test_runs() -> None:\n    add(2, 3)\n"
)


async def run(code: str, tests: str):
    res = await LocalSandbox().execute_code_async(
        build_test_script(code, tests, key=KEY), timeout=60
    )
    return parse_report(res.stdout, res.stderr, res.exit_code, key=KEY)


async def test_the_green_path_stays_green_and_counts_its_checks():
    report = await run(GOOD_ADD, ADD_TESTS)
    assert report.green and report.reason == "" and report.problem == ""
    assert (report.collected, report.assertions, report.unasserted) == (2, 2, 0)


async def test_a_test_that_asserts_nothing_is_not_verified():
    report = await run(GOOD_ADD, NO_ASSERT_TESTS)
    assert report.passed == report.total == 1 and not report.green
    assert report.reason == "no_assertions" and report.unasserted == 1
    assert "executed no assertion" in report.evidence()


async def test_one_unasserted_test_among_asserted_ones_is_enough_to_refuse():
    report = await run(GOOD_ADD, ADD_TESTS + NO_ASSERT_TESTS.split("\n\n\n", 1)[1])
    assert report.passed == 3 and report.unasserted == 1
    assert report.reason == "no_assertions" and not report.green


async def test_an_assert_in_the_code_under_test_is_not_a_check():
    code = "def add(a, b):\n    assert isinstance(a, int)\n    return a + b\n"
    report = await run(code, NO_ASSERT_TESTS)
    assert report.reason == "no_assertions"


async def test_pytest_raises_is_a_check():
    code = GOOD_ADD + "\n\ndef div(a, b):\n    return a / b\n"
    tests = (
        "import pytest\nfrom solution import div\n\n\n"
        "def test_div_by_zero() -> None:\n"
        "    with pytest.raises(ZeroDivisionError):\n        div(1, 0)\n"
    )
    report = await run(code, tests)
    assert report.green and report.assertions == 1, report.evidence()


async def test_an_unexpected_xpass_is_never_green():
    tests = (
        "import pytest\nfrom solution import add\n\n\n"
        "@pytest.mark.xfail(reason='flaky')\n"
        "def test_adds() -> None:\n    assert add(2, 3) == 5\n"
    )
    report = await run(GOOD_ADD, tests)
    assert report.passed == 0 and report.skipped == 1 and not report.green


async def test_a_teardown_error_after_a_pass_is_one_errored_test():
    tests = (
        "import pytest\nfrom solution import add\n\n\n"
        "@pytest.fixture\ndef broken():\n    yield\n    raise RuntimeError('teardown')\n\n\n"
        "def test_adds(broken) -> None:\n    assert add(2, 3) == 5\n"
    )
    report = await run(GOOD_ADD, tests)
    assert (report.passed, report.errors, report.collected) == (0, 1, 1)
    assert not report.green


def _line(**overrides: object) -> str:
    fields: dict[str, object] = {
        "failures": "[]",
        "passed": 2,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
        "collected": 2,
        "assertions": 2,
        "unasserted": 0,
        "collection_error": "false",
        "exit": 0,
    }
    fields.update(overrides)
    body = ", ".join(f'"{k}": {v}' for k, v in fields.items() if v is not None)
    return report_marker(KEY) + "{" + body + "}"


def test_a_consistent_report_is_verified():
    assert parse_report(_line(), "", 0, key=KEY).green


@pytest.mark.parametrize(
    "overrides",
    [
        {"collected": 3},  # a collected test the report does not account for
        {"collected": 1},  # more outcomes than tests
        {"collected": None},  # no count at all
        {"assertions": 1},  # fewer checks than asserted tests
        {"assertions": None},
        {"unasserted": None},
        {"unasserted": 3},  # more unasserted tests than passing ones
        {"unasserted": -1},
        {"failed": -1, "errors": 1},  # a negative count that balances the rest
    ],
)
def test_inconsistent_counts_are_not_verified(overrides):
    report = parse_report(_line(**overrides), "", 0, key=KEY)
    assert not report.green and report.reason == "inconsistent_report"
    assert "does not add up" in report.evidence()


def test_a_report_of_unasserted_tests_is_not_verified():
    report = parse_report(_line(unasserted=1, assertions=1), "", 0, key=KEY)
    assert not report.green and report.reason == "no_assertions"


def test_more_than_one_keyed_report_is_tampering():
    report = parse_report(f"{_line()}\n{_line()}\n", "", 0, key=KEY)
    assert not report.green and report.reason == "multiple_reports"


async def _workspace_run(tests: str, test_path: str):
    host = stage({"mod.py": "def f():\n    return 2\n", test_path: tests})
    res = await LocalSandbox().execute_code_async(
        build_workspace_script(test_path, ("",), key=KEY),
        mounts={host: "/work"},
        mounts_read_only=True,
    )
    return parse_report(res.stdout, res.stderr, res.exit_code, key=KEY)


async def test_a_workspace_test_file_with_any_name_has_its_asserts_counted():
    tests = "from mod import f\n\n\ndef test_f():\n    assert f() == 2\n"
    report = await _workspace_run(tests, "checks/verify_mod.py")
    assert report.green and report.assertions == 1, report.evidence()


async def test_a_workspace_test_that_asserts_nothing_is_not_verified():
    tests = "from mod import f\n\n\ndef test_f():\n    f()\n"
    report = await _workspace_run(tests, "tests/test_mod.py")
    assert report.reason == "no_assertions" and not report.green


@pytest.mark.parametrize(
    ("tests", "checks"),
    [
        (ADD_TESTS, True),
        (NO_ASSERT_TESTS, False),
        (
            "import pytest\n\n\ndef test_x():\n    with pytest.raises(ValueError):\n"
            "        int('x')\n",
            True,
        ),
        ("from pytest import approx\n\n\ndef test_x():\n    approx(1.0)\n", True),
        ("def test_x(:\n", True),  # unparseable: the run's collection error says more
        ("X = 1\n", False),
    ],
)
def test_the_static_check_finds_asserts_and_pytest_checks(tests, checks):
    assert checks_something(tests) is checks


def _agent(answers):
    a = CodingAgent(max_fix_attempts=3, execution_timeout=60)
    llm, box = ScriptedLLM(answers), LocalSandbox()
    a._llm, a._sandbox = llm, box
    return a, llm, box


def _both(tests: str) -> dict:
    return {"code": GOOD_ADD, "tests": tests, "explanation": "add", "changes": []}


async def test_a_first_test_file_without_asserts_is_asked_for_again_not_frozen():
    a, llm, box = _agent([_both(NO_ASSERT_TESTS), _both(ADD_TESTS)])
    r = await a.fix_code(BAD_ADD, "add(2, 3) returns -1")
    assert r.success and r.test_code == ADD_TESTS and r.iterations == 1
    assert len(llm.prompts) == 2 and NO_CHECKS.split(":")[0] in llm.prompts[1]
    assert (
        box.calls == 2
    )  # the attempt and the reproduction run, never the checkless file


async def test_a_model_that_never_asserts_never_gets_a_verified_run():
    a, llm, _ = _agent([_both(NO_ASSERT_TESTS)] * 9)
    r = await a.generate_code("a function add(a, b)")
    assert not r.success and "executed no assertion" in (r.error or "")
    # Never frozen: every attempt asked for new tests (three asks per attempt).
    assert all("These tests are fixed" not in p for p in llm.prompts)


async def test_a_workspace_fix_re_asks_for_tests_that_check_nothing():
    ws = Workspace(
        {"pkg/mod.py": "def quad(x):\n    return x * 3\n"},
        "pkg/mod.py",
        "tests/test_fix_mod.py",
    )
    fixed = "def quad(x):\n    return x * 4\n"
    checkless = "from pkg.mod import quad\n\n\ndef test_quad():\n    quad(1)\n"
    good = "from pkg.mod import quad\n\n\ndef test_quad():\n    assert quad(1) == 4\n"
    answers = [
        {"code": fixed, "tests": t, "explanation": "x4", "changes": []}
        for t in (checkless, good)
    ]
    a, llm, _ = _agent(answers)
    out = await a.fix_in_workspace(ws, "quad(1) returns 3, expected 4")
    assert out.success and out.verified and out.test_code == good
    assert NO_CHECKS.split(":")[0] in llm.prompts[1]
