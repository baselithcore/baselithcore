"""The workspace harness: files staged on the host, read-only, copied to /tmp, pytest in-process."""

from __future__ import annotations

import os

import pytest

from baselith.plugins.coding_agent.runner.harness import parse_report, screen_texts
from baselith.plugins.coding_agent.workspace.harness import (
    WORKSPACE_MAX_BYTES,
    build_workspace_script,
    stage,
)

from .coding_agent_fakes import LocalSandbox

UTIL = "def double(x):\n    return 2 * x\n"
MOD = "from pkg.util import double\n\n\ndef quad(x):\n    return double(double(x))\n"
TESTS = "from pkg.mod import quad\n\n\ndef test_quad():\n    assert quad(1) == 4\n"
KEY = "k" * 32


async def _run(files, test_path, roots=("",)):
    host = stage(files)
    sandbox = LocalSandbox()
    run = await sandbox.execute_code_async(
        build_workspace_script(test_path, roots, key=KEY),
        mounts={host: "/work"},
        mounts_read_only=True,
    )
    return parse_report(run.stdout, run.stderr, run.exit_code, key=KEY)


async def test_tests_import_the_real_module_path():
    report = await _run(
        {"pkg/util.py": UTIL, "pkg/mod.py": MOD, "tests/test_fix_mod.py": TESTS},
        "tests/test_fix_mod.py",
    )
    assert report.green and report.total == 1


async def test_a_src_layout_imports_through_its_root():
    files = {f"src/{k}": v for k, v in {"pkg/util.py": UTIL, "pkg/mod.py": MOD}.items()}
    files["tests/test_fix_mod.py"] = TESTS
    report = await _run(files, "tests/test_fix_mod.py", roots=("", "src"))
    assert report.green


async def test_a_missing_third_party_module_is_a_collection_error():
    report = await _run(
        {
            "pkg/mod.py": "import requests_nope\n",
            "tests/t_test.py": "import pkg.mod\n\n\ndef test_x():\n    pass\n",
        },
        "tests/t_test.py",
    )
    assert report.collection_error and "requests_nope" in report.evidence()


def test_stage_refuses_escapes_and_oversize():
    for bad in ("/etc/x.py", "../x.py", "a/../../x.py", ""):
        with pytest.raises(ValueError):
            stage({bad: "x"})
    with pytest.raises(ValueError, match="too large"):
        stage({"big.py": "x" * (WORKSPACE_MAX_BYTES + 1)})


def test_stage_is_not_writable_by_others():
    host = stage({"a.py": "x = 1\n"})
    assert os.stat(host).st_mode & 0o022 == 0


def test_the_script_refuses_an_escaping_test_path():
    with pytest.raises(ValueError):
        build_workspace_script("../t.py", ("",), key=KEY)


def test_the_main_idiom_is_not_refused_but_tampering_is():
    assert screen_texts(['if __name__ == "__main__":\n    main()\n']) is None
    assert screen_texts(["if __name__ == '__main__':\n    main()\n"]) is None
    assert screen_texts(["import atexit\n"]) is not None
    assert screen_texts(["import __main__\n"]) is not None
    assert screen_texts(["x = 1\n", "open('/proc/self/environ')\n"]) is not None


def test_only_the_main_guard_statement_is_allowed():
    # The allowance is the statement at the start of a line, not the expression anywhere.
    assert (
        screen_texts(["x = sys.modules[__name__ == '__main__' and '__main__']\n"])
        is not None
    )
    assert screen_texts(["y = (__name__ == '__main__')\n"]) is not None
    assert screen_texts(["    if __name__ == '__main__' :\n        run()\n"]) is None


@pytest.mark.parametrize(
    "name", ["conftest.py", "pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml"]
)
def test_stage_refuses_files_that_configure_pytest(name):
    with pytest.raises(ValueError, match="configure"):
        stage({f"tests/{name}": "x"})


async def test_a_conftest_cannot_reach_the_run(tmp_path):
    # Even if one slipped in, pytest runs with --noconftest and an empty config file.
    script = build_workspace_script("t_test.py", ("",), key=KEY)
    assert "--noconftest" in script and "os.devnull" in script


def test_the_sandbox_user_can_enter_the_staged_workspace():
    # The container runs as uid 65534: on Linux a bind mount keeps host modes, so the
    # directory must be traversable by others (its random name stays unguessable).
    host = stage({"pkg/a.py": "x = 1\n"})
    assert os.stat(host).st_mode & 0o005 == 0o005
    assert os.stat(os.path.join(host, "pkg")).st_mode & 0o005 == 0o005
    assert os.stat(os.path.join(host, "pkg", "a.py")).st_mode & 0o004


def test_other_users_cannot_list_where_workspaces_live():
    host = stage({"a.py": "x = 1\n"})
    parent = os.path.dirname(host)
    assert os.stat(parent).st_mode & 0o777 == 0o701
    assert os.stat(parent).st_uid == os.getuid()
    assert os.stat(host).st_mode & 0o077 == 0o005


def test_a_parent_owned_by_someone_else_is_refused(monkeypatch, tmp_path):
    from baselith.plugins.coding_agent.workspace import harness as workspace_harness

    trap = tmp_path / "planted"
    trap.symlink_to(tmp_path)
    monkeypatch.setattr(workspace_harness, "_parent_dir", lambda: str(trap))
    with pytest.raises(ValueError, match="staging"):
        stage({"a.py": "x"})


@pytest.mark.parametrize(
    "text",
    [
        'pytest_plugins = ["pkg.helper"]\n',
        "def pytest_runtest_logreport(report):\n    report.outcome = 'passed'\n",
        "import pytest\n\n@pytest.hookimpl(tryfirst=True)\ndef anything(item):\n    pass\n",
    ],
)
def test_files_that_plug_into_pytest_are_refused(text):
    assert screen_texts([text]) is not None


async def test_a_test_file_cannot_load_a_workspace_module_as_a_pytest_plugin():
    forger = (
        "import pytest\n\n\n@pytest.hookimpl(hookwrapper=True)\n"
        "def pytest_runtest_makereport(item, call):\n"
        "    outcome = yield\n    outcome.get_result().outcome = 'passed'\n"
    )
    tests = 'pytest_plugins = ["pkg.forger"]\n\n\ndef test_x():\n    assert False\n'
    from baselith.plugins.coding_agent import CodingAgent, Workspace

    agent = CodingAgent()
    agent._sandbox = LocalSandbox()
    ws = Workspace(
        {"pkg/forger.py": forger, "pkg/mod.py": "x = 1\n"},
        "pkg/mod.py",
        "tests/t_test.py",
    )
    report = await agent._run_workspace_tests(ws, "x = 1\n", tests)
    assert not report.green and "refused" in report.problem
    assert agent._sandbox.calls == 0
