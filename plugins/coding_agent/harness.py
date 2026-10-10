"""The sandbox harness: one ``python -c`` script that runs code plus tests with pytest.

The sandbox executes a single command with a read-only root filesystem, ``/tmp`` as the only
writable path and no network. The script therefore writes ``solution.py`` and
``test_solution.py`` into a private directory under ``/tmp``, runs ``pytest.main`` in-process with
a small reporting plugin (no cache, no bytecode), and prints exactly one marker line holding the
counts and the first failures as JSON. :func:`parse_report` turns the run back into a
:class:`TestReport`; a run that never printed the marker (a timeout, a crash, an import that
killed the interpreter) is an error report, never a pass.

The script imports nothing the sandbox's static analysis flags (``subprocess``, ``socket``,
``ctypes``); the code under test is the caller's business.

The code under test runs in the same process, so it could print a report of its own. Each run
therefore carries a fresh random key in its marker (:func:`new_run_key`), a report without the key
is ignored, and two reports with it are tampering. The key is in the interpreter's command line,
so :func:`screen` refuses code that reads ``/proc``, exits the interpreter early or registers exit
hooks before anything runs. This raises the bar; it is not a proof against deliberate tampering by
code that runs inside the sandbox, which is why the security review still reads every result.
"""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass, field

MARKER = "@@CODING_AGENT_REPORT@@"
#: What the code under test may not do: read the interpreter's command line or environment
#: (where the run key is), end the process before the report, or print after it.
_SCREENED = re.compile(
    r"/proc\b|\b_exit\s*\(|\batexit\b|CODING_AGENT_REPORT|__main__"
    r"|\bget_referrers\b|\bget_objects\b|\bsys\._getframe\b|\bpluginmanager\b"
)
#: Characters of one failure's traceback kept in the report.
FAILURE_CHARS = 700
#: Failures carried back in full; the rest are counted.
MAX_FAILURES = 5

_TEMPLATE = """\
import json, os, sys, tempfile
sys.dont_write_bytecode = True
_root = tempfile.mkdtemp(prefix="coding-agent-", dir="/tmp")
with open(os.path.join(_root, "solution.py"), "w", encoding="utf-8") as _f:
    _f.write({code!r})
with open(os.path.join(_root, "test_solution.py"), "w", encoding="utf-8") as _f:
    _f.write({tests!r})
os.chdir(_root)
sys.path.insert(0, _root)
import pytest

class _Report:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = 0
        self.collection_error = False
        self.failures = []

    def _keep(self, name, longrepr):
        if len(self.failures) < {max_failures}:
            self.failures.append([name, str(longrepr)[-{failure_chars}:]])

    def pytest_runtest_logreport(self, report):
        if report.when == "call" and report.passed:
            self.passed += 1
        elif report.failed:
            if report.when == "call":
                self.failed += 1
            else:
                self.errors += 1
            self._keep(report.nodeid.split("::")[-1], report.longrepr)

    def pytest_collectreport(self, report):
        if report.failed:
            self.collection_error = True
            self._keep("collection", report.longrepr)

def _main():
    # Locals only: the code under test finds no module global to rewrite.
    report = _Report()
    code = pytest.main(
        ["-q", "-p", "no:cacheprovider", "--no-header", "-o",
         "console_output_style=classic", "test_solution.py"],
        plugins=[report],
    )
    sys.stdout.flush()
    print({marker!r} + json.dumps({{
        "passed": report.passed, "failed": report.failed, "errors": report.errors,
        "collection_error": report.collection_error, "failures": report.failures,
        "exit": int(code),
    }}))

_main()
"""


@dataclass(frozen=True, slots=True)
class TestReport:
    """What one run of the tests established.

    Attributes:
        passed: Tests that passed.
        failed: Tests whose body failed.
        errors: Tests whose setup or teardown failed.
        collection_error: The test file (or the code it imports) could not be collected.
        failures: ``(test name, traceback tail)`` for the first failures.
        problem: Why there is no report at all (timeout, crash), or "".
        seconds: Wall-clock time of the sandbox run.
    """

    __test__ = False  # not a pytest test class, whatever its name

    passed: int = 0
    failed: int = 0
    errors: int = 0
    collection_error: bool = False
    failures: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    problem: str = ""
    seconds: float = 0.0

    @property
    def total(self) -> int:
        """Tests that ran (collection errors are not tests)."""
        return self.passed + self.failed + self.errors

    @property
    def green(self) -> bool:
        """At least one test ran and every one passed."""
        return (
            not self.problem
            and not self.collection_error
            and self.total > 0
            and self.passed == self.total
        )

    def evidence(self) -> str:
        """A short, deterministic account of the run: what the next attempt needs to know."""
        if self.problem:
            return self.problem
        if self.green:
            return f"{self.passed} of {self.total} tests passed"
        head = (
            "the test file could not be collected"
            if self.collection_error
            else "no test ran (no test_* function was collected)"
            if self.total == 0
            else f"{self.total - self.passed} of {self.total} tests failed"
        )
        details = "\n".join(f"- {name}: {tail.strip()}" for name, tail in self.failures)
        return f"{head}\n{details}".strip()


def new_run_key() -> str:
    """A fresh secret for one run's report marker."""
    return secrets.token_hex(16)


def report_marker(key: str) -> str:
    """The marker a run keyed ``key`` prints its report after."""
    return f"{MARKER[:-2]}:{key}@@"


def screen(code: str, tests: str) -> str | None:
    """Why ``code`` or ``tests`` may not run in the harness, or None."""
    for label, text in (("code", code), ("tests", tests)):
        found = _SCREENED.search(text or "")
        if found:
            return (
                f"the {label} use {found.group(0)!r}, which could tamper with the test "
                "report; write it without reading /proc, os._exit or atexit"
            )
    return None


def build_test_script(code: str, tests: str, *, key: str) -> str:
    """The ``python -c`` script that runs ``tests`` against ``code`` (as ``solution.py``)."""
    return _TEMPLATE.format(
        code=code,
        tests=tests,
        marker=report_marker(key),
        max_failures=MAX_FAILURES,
        failure_chars=FAILURE_CHARS,
    )


def _tail(text: str, chars: int = FAILURE_CHARS) -> str:
    return text.strip()[-chars:]


def parse_report(
    stdout: str, stderr: str, exit_code: int, seconds: float = 0.0, *, key: str
) -> TestReport:
    """The report the run keyed ``key`` printed, or an error report saying why there is none."""
    marker = report_marker(key)
    lines = [line for line in (stdout or "").splitlines() if line.startswith(marker)]
    if len(lines) > 1:
        return TestReport(
            problem="the run printed more than one report: the code under test tampered with it",
            seconds=seconds,
        )
    for line in lines:
        if line.startswith(marker):
            try:
                data = json.loads(line[len(marker) :])
                return TestReport(
                    passed=int(data["passed"]),
                    failed=int(data["failed"]),
                    errors=int(data["errors"]),
                    collection_error=bool(data["collection_error"]),
                    failures=tuple((str(n), str(t)) for n, t in data["failures"]),
                    seconds=seconds,
                )
            except (ValueError, KeyError, TypeError):
                break
    if exit_code == 124:
        problem = "the run ran out of time in the sandbox"
    elif stderr and stderr.strip():
        problem = f"the run crashed before reporting:\n{_tail(stderr)}"
    else:
        problem = f"the run produced no report (exit code {exit_code})"
    return TestReport(problem=problem, seconds=seconds)


__all__ = [
    "MARKER",
    "TestReport",
    "build_test_script",
    "new_run_key",
    "parse_report",
    "report_marker",
    "screen",
]
