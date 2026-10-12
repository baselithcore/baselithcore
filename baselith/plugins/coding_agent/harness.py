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
is ignored, two reports with it are tampering, and a report followed by a non-zero process exit
(the process killed or exited after a forged line) is too. The key is in the interpreter's command
line, so :func:`~baselith.plugins.coding_agent.screening.screen` refuses code that could read it, reach the
reporter or plug into pytest before anything runs; the report line is written with functions bound
before any test code is imported, and a skipped test or a session pytest ended early is never
green. A green report must also add up (every collected test accounted for, at least one
passed) and every passing test must have executed an assertion; otherwise the run is not
verified, with the reason. This raises the bar; it is not a proof against deliberate tampering
by code that runs inside the sandbox, which is why the security review still reads every result.

"Verified" means the code passed the model's own tests under this screen, nothing more: the
model writes both, so it is not proof the code is correct.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field, replace

from baselith.plugins.coding_agent.screening import screen, screen_texts

MARKER = "@@CODING_AGENT_REPORT@@"
#: Characters of one failure's traceback kept in the report.
FAILURE_CHARS = 700
#: Failures carried back in full; the rest are counted.
MAX_FAILURES = 5
#: The ini override that makes pytest call ``pytest_assertion_pass`` for every passing ``assert``
#: in a rewritten test module: how the reporter counts the checks a test executed.
ASSERTION_HOOK_OPTION = "enable_assertion_pass_hook=true"

#: The reporter and the entry point both harness scripts end with. ``{args}`` is pytest's argv.
#: Everything the report line is made with is bound to a local before pytest imports any test
#: code, and the counts are formatted as integers, never through ``json``: a test that patches
#: ``print``, ``json``, ``int`` or ``sys.stdout`` changes nothing the line says. A skipped test,
#: and a session pytest ended early (``KeyboardInterrupt``, ``pytest.exit``), are counted too.
#: Each test gets one outcome (the worst of its phases) and a count of the checks it executed:
#: ``assert`` statements through pytest's assertion-pass hook (``enable_assertion_pass_hook``,
#: set on the command line) plus calls to ``pytest.raises``/``pytest.warns``, wrapped before any
#: test code is imported. :func:`parse_report` holds a green run to those counts.
RUNNER = """\
class _Report:
    def __init__(self):
        self.outcomes = {{}}
        self.checks = {{}}
        self.current = None
        self.collected = 0
        self.skipped_files = 0
        self.collection_error = False
        self.failures = []

    def _keep(self, name, longrepr):
        if len(self.failures) < {max_failures}:
            self.failures.append([name, str(longrepr)[-{failure_chars}:]])

    def check(self, nodeid):
        if nodeid is not None:
            self.checks[nodeid] = self.checks.get(nodeid, 0) + 1

    def pytest_collection_finish(self, session):
        self.collected = len(session.items)

    def pytest_runtest_logstart(self, nodeid):
        self.current = nodeid

    def pytest_runtest_logfinish(self, nodeid):
        self.current = None

    def pytest_assertion_pass(self, item):
        self.check(item.nodeid)

    def pytest_runtest_logreport(self, report):
        # One outcome per test, the worst of its phases: a test that passes and then fails
        # its teardown is an error, an xpass proves nothing like a skip.
        name = report.nodeid.split("::")[-1]
        if report.failed:
            kind = 2 if report.when == "call" else 3
            self._keep(name, report.longrepr)
        elif report.skipped or (report.when == "call" and hasattr(report, "wasxfail")):
            kind = 1
            self._keep(name, "skipped" if report.skipped else "xpassed")
        elif report.when == "call" and report.passed:
            kind = 0
        else:
            return
        if kind > self.outcomes.get(report.nodeid, -1):
            self.outcomes[report.nodeid] = kind

    def pytest_collectreport(self, report):
        if report.failed:
            self.collection_error = True
            self._keep("collection", report.longrepr)
        elif report.skipped:
            self.skipped_files += 1
            self._keep("collection", "skipped")

def _main():
    # Locals only: the code under test finds no module global to rewrite.
    write, dumps, num, size, add_up = os.write, json.dumps, int, len, sum
    out = os.dup(1)
    report = _Report()
    # pytest.raises and pytest.warns are checks too: counted for the running test.
    def _counted(check):
        def counted(*args, **kwargs):
            report.check(report.current)
            return check(*args, **kwargs)
        return counted
    pytest.raises, pytest.warns = _counted(pytest.raises), _counted(pytest.warns)
    code = pytest.main({args}, plugins=[report])
    sys.stdout.flush()
    kinds = [k for k in report.outcomes.values()]
    passed = [n for n, k in report.outcomes.items() if k == 0]
    asserted = [n for n in passed if report.checks.get(n, 0) > 0]
    checks = add_up(report.checks.get(n, 0) for n in passed)
    ce = "true" if report.collection_error is True else "false"
    # The failures come first: a later duplicate key wins, so a forged fragment cannot
    # override the counts.
    line = (
        {marker!r} + '{{"failures": ' + dumps(report.failures)
        + f', "passed": {{num(size(passed)):d}}, "failed": {{num(kinds.count(2)):d}}'
        + f', "errors": {{num(kinds.count(3)):d}}, "skipped": {{num(kinds.count(1)):d}}'
        + f', "skipped_files": {{num(report.skipped_files):d}}'
        + f', "collected": {{num(report.collected):d}}, "assertions": {{num(checks):d}}'
        + f', "unasserted": {{num(size(passed) - size(asserted)):d}}'
        + f', "collection_error": {{ce}}, "exit": {{num(code):d}}' + "}}\\n"
    )
    write(out, line.encode("utf-8"))

_main()
"""

_TEMPLATE = """\
import json, os, sys, tempfile
sys.dont_write_bytecode = True
os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
os.environ.pop("PYTEST_ADDOPTS", None)
import pytest
_root = tempfile.mkdtemp(prefix="coding-agent-", dir="/tmp")
with open(os.path.join(_root, "solution.py"), "w", encoding="utf-8") as _f:
    _f.write({code!r})
with open(os.path.join(_root, "test_solution.py"), "w", encoding="utf-8") as _f:
    _f.write({tests!r})
os.chdir(_root)
sys.path.insert(0, _root)
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
        skipped: Tests (or a test file) pytest skipped, marked xfail or that xpassed: a skip
            proves nothing, so a run with one is never green.
        exit_status: pytest's exit status; anything but 0 with every test passing means the
            session ended early (``KeyboardInterrupt``, ``pytest.exit``).
        problem: Why the run is not verified whatever its counts (timeout, crash, tampering, an
            inconsistent report, passing tests that checked nothing), or "".
        seconds: Wall-clock time of the sandbox run.
        collected: Tests pytest collected; a green run accounts for every one of them.
        assertions: Checks (``assert`` statements, ``pytest.raises``/``pytest.warns``) the
            passing tests executed.
        unasserted: Passing tests that executed no check at all.
        reason: A short code for ``problem``: ``timeout``, ``crash``, ``no_report``,
            ``multiple_reports``, ``exit_after_report``, ``inconsistent_report``,
            ``no_assertions``, ``refused`` (the screen or the workspace stager refused the
            files), or "".
    """

    __test__ = False  # not a pytest test class, whatever its name

    passed: int = 0
    failed: int = 0
    errors: int = 0
    collection_error: bool = False
    failures: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    skipped: int = 0
    exit_status: int = 0
    problem: str = ""
    seconds: float = 0.0
    collected: int = 0
    assertions: int = 0
    unasserted: int = 0
    reason: str = ""

    @property
    def total(self) -> int:
        """Tests that ran (collection errors are not tests)."""
        return self.passed + self.failed + self.errors

    @property
    def green(self) -> bool:
        """At least one test ran, every one passed, none was skipped and pytest ended cleanly."""
        return (
            not self.problem
            and not self.collection_error
            and self.total > 0
            and self.passed == self.total
            and self.skipped == 0
            and self.exit_status == 0
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
            if self.passed < self.total
            else f"{self.skipped} tests were skipped; a skipped test proves nothing"
            if self.skipped
            else f"pytest ended the session early (exit status {self.exit_status})"
        )
        details = "\n".join(f"- {name}: {tail.strip()}" for name, tail in self.failures)
        return f"{head}\n{details}".strip()


def new_run_key() -> str:
    """A fresh secret for one run's report marker."""
    return secrets.token_hex(16)


def report_marker(key: str) -> str:
    """The marker a run keyed ``key`` prints its report after."""
    return f"{MARKER[:-2]}:{key}@@"


def build_test_script(code: str, tests: str, *, key: str) -> str:
    """The ``python -c`` script that runs ``tests`` against ``code`` (as ``solution.py``)."""
    return _TEMPLATE.format(code=code, tests=tests) + runner_source(
        '["-q", "-p", "no:cacheprovider", "--no-header", "-o", '
        f'"console_output_style=classic", "-o", {ASSERTION_HOOK_OPTION!r}, "test_solution.py"]',
        key=key,
    )


def runner_source(args: str, *, key: str) -> str:
    """:data:`RUNNER` for pytest argv ``args`` (Python source) and the run keyed ``key``."""
    return RUNNER.format(
        args=args,
        marker=report_marker(key),
        max_failures=MAX_FAILURES,
        failure_chars=FAILURE_CHARS,
    )


def _tail(text: str, chars: int = FAILURE_CHARS) -> str:
    return text.strip()[-chars:]


def _verified(report: TestReport, item_skipped: int) -> TestReport:
    """``report`` when its counts hold together, else the same counts marked not verified.

    Only a green run is checked: anything else is already not verified, with its own evidence.
    A green run accounts for every collected test (passed + failed + errors + skipped), at least
    one test passed, and every passing test executed at least one check.
    """
    if not report.green:
        return report
    counts = (
        report.passed,
        report.failed,
        report.errors,
        item_skipped,
        report.collected,
    )
    ran = report.passed + report.failed + report.errors + item_skipped
    if (
        min(counts) < 0
        or report.collected != ran
        or report.passed < 1
        or not 0 <= report.unasserted <= report.passed
        or report.assertions < report.passed - report.unasserted
    ):
        return replace(
            report,
            problem=(
                f"the report does not add up ({report.collected} collected, "
                f"{report.passed} passed, {report.failed} failed, {report.errors} errors, "
                f"{item_skipped} skipped, {report.assertions} checks): the run is not "
                "verified, the report was tampered with or is invalid"
            ),
            reason="inconsistent_report",
        )
    if report.unasserted:
        return replace(
            report,
            problem=(
                f"{report.unasserted} of {report.passed} passing tests executed no assertion "
                "(no assert, pytest.raises or pytest.warns ran): a test that checks nothing "
                "proves nothing; every test must assert the behaviour it pins"
            ),
            reason="no_assertions",
        )
    return report


def parse_report(
    stdout: str, stderr: str, exit_code: int, seconds: float = 0.0, *, key: str
) -> TestReport:
    """The report the run keyed ``key`` printed, or an error report saying why there is none.

    A green report is also held to its own counts (:func:`_verified`): one that does not add up,
    or whose passing tests executed no assertion, comes back not verified with the reason.
    """
    marker = report_marker(key)
    lines = [line for line in (stdout or "").splitlines() if line.startswith(marker)]
    if len(lines) > 1:
        return TestReport(
            problem="the run printed more than one report: the code under test tampered with it",
            seconds=seconds,
            reason="multiple_reports",
        )
    if lines and exit_code != 0:
        # The harness ends normally after its report: a process killed or exited with another
        # status after it (or after a forged one) did not finish the run it reports.
        return TestReport(
            problem=f"the run exited with code {exit_code} after its report: the code under "
            "test ended the process",
            seconds=seconds,
            reason="exit_after_report",
        )
    for line in lines:
        try:
            data = json.loads(line[len(marker) :])
            item_skipped = int(data.get("skipped", 0))
            report = TestReport(
                passed=int(data["passed"]),
                failed=int(data["failed"]),
                errors=int(data["errors"]),
                collection_error=bool(data["collection_error"]),
                failures=tuple((str(n), str(t)) for n, t in data["failures"]),
                skipped=item_skipped + int(data.get("skipped_files", 0)),
                exit_status=int(data.get("exit", 0)),
                seconds=seconds,
                # A report without these is never verified: -1 cannot add up.
                collected=int(data.get("collected", -1)),
                assertions=int(data.get("assertions", -1)),
                unasserted=int(data.get("unasserted", -1)),
            )
        except (ValueError, KeyError, TypeError, AttributeError):
            break
        return _verified(report, item_skipped)
    if exit_code == 124:
        problem, reason = "the run ran out of time in the sandbox", "timeout"
    elif stderr and stderr.strip():
        problem, reason = f"the run crashed before reporting:\n{_tail(stderr)}", "crash"
    else:
        problem = f"the run produced no report (exit code {exit_code})"
        reason = "no_report"
    return TestReport(problem=problem, seconds=seconds, reason=reason)


__all__ = [
    "ASSERTION_HOOK_OPTION",
    "FAILURE_CHARS",
    "MARKER",
    "MAX_FAILURES",
    "TestReport",
    "build_test_script",
    "new_run_key",
    "parse_report",
    "report_marker",
    "runner_source",
    "screen",
    "screen_texts",
]
