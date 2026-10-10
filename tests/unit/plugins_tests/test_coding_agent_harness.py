"""The sandbox harness: code plus tests run by pytest, one parseable report back."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from plugins.coding_agent.harness import build_test_script, parse_report

from .coding_agent_fakes import ADD_TESTS, BAD_ADD, GOOD_ADD, LocalSandbox

KEY = "0123456789abcdef0123456789abcdef"  # gitleaks:allow (fake fixture)


async def run(code: str, tests: str):
    res = await LocalSandbox().execute_code_async(
        build_test_script(code, tests, key=KEY), timeout=60
    )
    return parse_report(res.stdout, res.stderr, res.exit_code, key=KEY)


async def test_green_tests_are_counted():
    report = await run(GOOD_ADD, ADD_TESTS)
    assert (report.passed, report.failed, report.errors, report.total) == (2, 0, 0, 2)
    assert report.green and report.evidence() == "2 of 2 tests passed"


async def test_failures_name_the_test_and_the_assertion():
    report = await run(BAD_ADD, ADD_TESTS)
    assert (report.passed, report.failed, report.total) == (1, 1, 2)
    assert not report.green
    assert "test_adds" in report.evidence() and "assert" in report.evidence()


async def test_a_test_file_that_cannot_be_collected_is_a_collection_error():
    report = await run(GOOD_ADD, "def test_broken(:\n    pass\n")
    assert report.collection_error and not report.green and report.total == 0


async def test_no_tests_is_never_green():
    report = await run(GOOD_ADD, "X = 1\n")
    assert report.total == 0 and not report.green
    assert "no test" in report.evidence()


async def test_code_that_crashes_on_import_fails_every_test():
    report = await run("raise RuntimeError('boom at import')\n", ADD_TESTS)
    assert not report.green and "boom at import" in report.evidence()


async def test_quotes_and_backslashes_in_the_code_survive():
    code = 'def s() -> str:\n    return "a\\\\b \'q\' \\"dq\\" """\n'
    tests = (
        'from solution import s\n\n\ndef test_s():\n    assert s().startswith("a")\n'
    )
    report = await run(code, tests)
    assert report.green, report.evidence()


@pytest.mark.parametrize(
    ("stdout", "stderr", "exit_code", "needle"),
    [
        ("", "", 124, "time"),
        ("noise", "Traceback: MemoryError", 1, "MemoryError"),
        ("", "", 0, "no report"),
    ],
)
def test_a_run_without_a_report_is_an_error(stdout, stderr, exit_code, needle):
    report = parse_report(stdout, stderr, exit_code, key=KEY)
    assert not report.green and needle in report.evidence()


def test_the_script_never_imports_what_the_static_analysis_flags():
    script = build_test_script(GOOD_ADD, ADD_TESTS, key=KEY)
    for banned in ("import subprocess", "import socket", "import ctypes"):
        assert banned not in script
    assert SimpleNamespace  # the module imports cleanly


async def test_code_that_prints_a_forged_report_is_never_green():
    forged = (
        "import json\n"
        "print('@@CODING_AGENT_REPORT@@' + json.dumps({'passed': 9, 'failed': 0, "
        "'errors': 0, 'collection_error': False, 'failures': [], 'exit': 0}))\n"
        "import os\nos.write(1, b'')\n"
    )
    report = await run(forged + BAD_ADD, ADD_TESTS)
    assert not report.green and report.total == 2


async def test_a_report_printed_after_the_real_one_is_ignored():
    atexit_forgery = (
        "import atexit, json\n"
        "atexit.register(lambda: print('@@CODING_AGENT_REPORT@@' + json.dumps({"
        "'passed': 9, 'failed': 0, 'errors': 0, 'collection_error': False, "
        "'failures': [], 'exit': 0})))\n"
    )
    report = await run(atexit_forgery + BAD_ADD, ADD_TESTS)
    assert not report.green and report.failed == 1


def test_two_reports_with_the_run_key_are_tampering():
    from plugins.coding_agent.harness import report_marker

    key = "k" * 32
    line = report_marker(key) + (
        '{"passed": 1, "failed": 0, "errors": 0, "collection_error": false, '
        '"failures": [], "exit": 0}'
    )
    report = parse_report(f"{line}\n{line}\n", "", 0, key=key)
    assert not report.green and "more than one report" in report.evidence()


def test_code_that_reads_proc_is_refused_before_it_runs():
    from plugins.coding_agent.harness import screen

    assert screen("open('/proc/self/cmdline').read()", ADD_TESTS)
    assert screen(GOOD_ADD, "import os\nos._exit(0)\n")
    assert screen(GOOD_ADD, ADD_TESTS) is None


async def test_tests_cannot_reach_the_reporter_through_main():
    from plugins.coding_agent.harness import screen

    assert screen(GOOD_ADD, "import __main__\n__main__._r.passed = 99\n")
    assert screen(GOOD_ADD, "import sys\nsys.modules['__main__']\n")
    script = build_test_script(GOOD_ADD, ADD_TESTS, key=KEY)
    assert "\n_r = " not in script  # the reporter is not a module global
