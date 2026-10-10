"""Ways the code under test could forge a green report, each one closed.

The screen tests check what is refused before anything runs; the run tests skip the screen
(they build the script directly) to prove the harness itself holds when a forgery gets past it.
"""

from __future__ import annotations

import pytest

from plugins.coding_agent.harness import (
    build_test_script,
    parse_report,
    report_marker,
    screen,
    screen_texts,
)
from plugins.coding_agent.workspace_harness import build_workspace_script, stage

from .coding_agent_fakes import ADD_TESTS, BAD_ADD, GOOD_ADD, LocalSandbox

KEY = "fedcba9876543210fedcba9876543210"  # gitleaks:allow (fake fixture)


async def run(code: str, tests: str = ADD_TESTS):
    res = await LocalSandbox().execute_code_async(
        build_test_script(code, tests, key=KEY), timeout=60
    )
    return parse_report(res.stdout, res.stderr, res.exit_code, key=KEY)


@pytest.mark.parametrize(
    "code",
    [
        # Names built from pieces, or spelled with escapes and lookalike letters.
        "m = __import__('o' + 's')\n",
        "import sys\nm = sys.modules['__ma' + 'in__']\n",
        "import sys\nm = getattr(sys, 'modu' + 'les')\n",
        "import sys\nm = (sys) . modules\n",
        "import sys\nm = sys.\\\nmodules\n",
        "open('/pr' + 'oc/self/cmdline').read()\n",
        "open('\\x2fproc/self/cmdline').read()\n",
        "open(''.join(['/p', 'roc', '/self/cmdline'])).read()\n",
        "open(chr(47) + 'proc/self/cmdline').read()\n",
        "open(f'{\"/pr\"}oc/self/cmdline').read()\n",
        "\uff45\uff58\uff45\uff43('x = 1')\n",
        # The run key in the interpreter's own command line.
        "import sys\nk = sys.orig_argv\n",
        # The harness's frames, code and objects.
        "import inspect\nf = inspect.currentframe().f_back\n",
        "import sys\ntry:\n    1 / 0\nexcept Exception as e:\n    f = e.__traceback__.tb_frame\n",
        "x = (lambda: 0).__globals__\n",
        "x = ().__class__.__base__.__subclasses__()\n",
        "import gc\nx = gc.get_referents(object)\n",
        # Code the screen never read, or another process in place of this one.
        "exec('x = 1')\n",
        "import importlib\nm = importlib.import_module('json')\n",
        "import builtins\nbuiltins.print = len\n",
        "import os\nos.execv('/bin/true', ['true'])\n",
        "import os\nos.system('true')\n",
        # pytest's machinery.
        "import pytest\npytest.Function.runtest = lambda self: None\n",
        "import _pytest.runner\n",
        "def test_x(request):\n    request.config.hook.pytest_runtest_logreport(report=None)\n",
        "def test_x(request):\n    request.node.ihook\n",
    ],
)
def test_a_forgery_spelled_another_way_is_still_refused(code):
    assert screen(code, ADD_TESTS) is not None
    assert screen(GOOD_ADD, code) is not None


@pytest.mark.parametrize(
    "tests",
    [
        ADD_TESTS,
        "import re\n\nPAT = re.compile('a+')\n\n\ndef test_re():\n    assert PAT.match('aa')\n",
        "def test_input(monkeypatch):\n"
        "    monkeypatch.setattr('builtins.input', lambda _: 'y')\n"
        "    assert input('?') == 'y'\n",
        "import pytest\n\n\n@pytest.mark.parametrize('x', [1, 2])\ndef test_p(x):\n"
        "    with pytest.raises(ZeroDivisionError):\n        x / 0\n",
        "def test_exec_word():\n    executor = 'not a call'\n    assert executor\n",
    ],
)
def test_ordinary_tests_pass_the_screen(tests):
    assert screen(GOOD_ADD, tests) is None


def test_the_main_guard_still_passes_in_a_repository_file():
    text = "def main():\n    pass\n\n\nif __name__ == '__main__':\n    main()\n"
    assert screen_texts([text]) is None
    assert screen_texts([text + "x = '__ma' + 'in__'\n"]) is not None


async def test_patching_json_and_print_does_not_change_the_counts():
    forgery = (
        "import builtins, json\n"
        "_d, _p = json.dumps, builtins.print\n"
        "json.dumps = lambda o, *a, **k: _d({**o, 'failed': 0, 'passed': 2} "
        "if isinstance(o, dict) else o, *a, **k)\n"
        "builtins.print = lambda *a, **k: _p(*(str(x).replace('\"failed\": 1', "
        "'\"failed\": 0') for x in a), **k)\n"
        'json.dumps = lambda o, *a, **k: \'[], "passed": 2, "failed": 0\'\n'
    )
    report = await run(forgery + BAD_ADD)
    assert not report.green and report.failed == 1


async def test_a_test_the_code_skips_is_never_green():
    code = (
        "import unittest\n\n\ndef add(a, b):\n"
        "    if (a, b) == (2, 3):\n        raise unittest.SkipTest('later')\n"
        "    return a - b\n"
    )
    report = await run(code)
    assert report.passed == report.total == 1 and report.skipped == 1
    assert not report.green and "skipped" in report.evidence()


async def test_a_session_ended_early_is_never_green():
    code = (
        "def add(a, b):\n"
        "    if (a, b) == (0, 0):\n        raise KeyboardInterrupt\n"
        "    return a + b\n"
    )
    tests = (
        "from solution import add\n\n\n"
        "def test_a() -> None:\n    assert add(2, 3) == 5\n\n\n"
        "def test_b() -> None:\n    assert add(0, 0) == 1\n"
    )
    report = await run(code, tests)
    assert report.exit_status != 0 and not report.green
    assert "early" in report.evidence()


async def test_a_forged_report_followed_by_a_kill_is_never_green():
    # The key read from the command line, a forged line written to every open descriptor
    # (one of them is the real stdout pytest saved), then the real report killed off.
    forgery = (
        "import os, re, signal, sys\n"
        "_k = re.search(r'@@:([0-9a-f]{32})@@', ' '.join(sys.orig_argv)).group(1)\n"
        "_m = '@@CODING_AGENT_REPORT:' + _k + '@@'\n"
        '_l = (_m + \'{"passed": 2, "failed": 0, "errors": 0, \'\n'
        '      \'"collection_error": false, "failures": [], "exit": 0}\\n\').encode()\n'
        "for _fd in range(1, 20):\n"
        "    try:\n        os.write(_fd, _l)\n    except OSError:\n        pass\n"
        "os.kill(os.getpid(), signal.SIGKILL)\n"
    )
    assert screen(forgery + BAD_ADD, ADD_TESTS) is not None
    report = await run(forgery + BAD_ADD)
    assert not report.green


def test_a_report_then_a_non_zero_exit_is_tampering():
    line = report_marker(KEY) + (
        '{"passed": 2, "failed": 0, "errors": 0, "skipped": 0, '
        '"collection_error": false, "failures": [], "exit": 0}'
    )
    assert parse_report(line, "", 0, key=KEY).green
    for code in (-9, 1, 137):
        report = parse_report(line, "", code, key=KEY)
        assert not report.green and "after its report" in report.problem


async def test_a_workspace_pytest_module_is_not_the_pytest_that_runs():
    fake = "def main(args, plugins):\n    plugins[0].passed = 1\n    return 0\n"
    files = {
        "pytest.py": fake,
        "mod.py": "def f():\n    return 1\n",
        "tests/t_test.py": "from mod import f\n\n\ndef test_f():\n    assert f() == 2\n",
    }
    host = stage(files)
    res = await LocalSandbox().execute_code_async(
        build_workspace_script("tests/t_test.py", ("",), key=KEY),
        mounts={host: "/work"},
        mounts_read_only=True,
    )
    report = parse_report(res.stdout, res.stderr, res.exit_code, key=KEY)
    assert report.failed == 1 and not report.green
