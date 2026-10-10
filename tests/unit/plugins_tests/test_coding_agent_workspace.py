"""fix_in_workspace: verified on a staged workspace, unverified when it cannot run."""

from __future__ import annotations

import pytest

from plugins.coding_agent import CodingAgent, Workspace

from .coding_agent_fakes import LocalSandbox, ScriptedLLM

UTIL = "def double(x):\n    return 2 * x\n"
BUGGY = (
    "from pkg.util import double\n\n\ndef quad(x):\n    return double(x) + 1\n\n\n"
    'if __name__ == "__main__":\n    print(quad(2))\n'
)
FIXED = (
    "from pkg.util import double\n\n\ndef quad(x):\n    return double(double(x))\n\n\n"
    'if __name__ == "__main__":\n    print(quad(2))\n'
)
TESTS = (
    "from pkg.mod import quad\n\n\ndef test_quad():\n    assert quad(1) == 4\n"
    "    assert quad(3) == 12\n"
)
WS = Workspace(
    {"pkg/util.py": UTIL, "pkg/mod.py": BUGGY}, "pkg/mod.py", "tests/test_fix_mod.py"
)


def agent(answers, sandbox=None) -> tuple[CodingAgent, ScriptedLLM]:
    a = CodingAgent(max_fix_attempts=2, execution_timeout=60)
    llm = ScriptedLLM(answers)
    a._llm, a._sandbox = llm, sandbox or LocalSandbox()
    return a, llm


def fix(code=FIXED, tests=TESTS, changes=("quad doubles twice",)):
    return {
        "code": code,
        "tests": tests,
        "explanation": "nested",
        "changes": list(changes),
    }


async def test_a_verified_fix_on_the_real_module_path():
    a, llm = agent([fix()])
    out = await a.fix_in_workspace(WS, "quad(1) returns 3, expected 4")
    assert out.success and out.verified and out.tests_passed == out.tests_total == 1
    assert out.tests_reproduce_bug is True
    assert out.final_code == FIXED and out.test_code == TESTS
    assert out.diff.startswith("--- a/pkg/mod.py\n+++ b/pkg/mod.py")
    assert (out.target, out.test_path) == ("pkg/mod.py", "tests/test_fix_mod.py")
    assert "pkg/util.py" in llm.prompts[0] and "solution" not in llm.systems[0]
    assert a._sandbox.mounts and all(ro for _m, ro in a._sandbox.mounts)


async def test_a_failing_fix_is_retried_with_the_tests_frozen():
    wrong = fix(code=BUGGY.replace("+ 1", "+ 2"))
    a, llm = agent([wrong, {"code": FIXED, "explanation": "fixed", "changes": []}])
    out = await a.fix_in_workspace(WS, "quad(1) returns 3")
    assert out.verified and out.success and out.iterations == 2
    assert TESTS.strip() in llm.prompts[1]


async def test_a_missing_dependency_falls_back_to_unverified():
    ws = Workspace(
        {"pkg/mod.py": "import requests_nope\n\n\ndef f():\n    return 1\n"},
        "pkg/mod.py",
        "tests/test_fix_mod.py",
    )
    answer = fix(
        code="import requests_nope\n\n\ndef f():\n    return 2\n",
        tests="def test_f():\n    pass\n",
    )
    a, _ = agent([answer])
    out = await a.fix_in_workspace(ws, "f returns 1")
    assert not out.verified and out.unverified_reason == "missing_dependency"
    assert (
        out.success and out.final_code.endswith("return 2\n") and out.tests_total == 0
    )
    assert out.test_code == "def test_f():\n    pass\n"


async def test_another_language_is_proposed_unverified():
    ws = Workspace(
        {"src/a.js": "export const add = (a, b) => a - b;\n"},
        "src/a.js",
        "src/a.test.js",
    )
    a, _ = agent(
        [
            fix(
                code="export const add = (a, b) => a + b;\n",
                tests="test('add', () => {});\n",
            )
        ]
    )
    out = await a.fix_in_workspace(ws, "add subtracts", language="javascript")
    assert (
        not out.verified
        and out.unverified_reason == "language"
        and a._sandbox.calls == 0
    )
    assert out.success and "+ b" in out.diff


async def test_an_oversize_workspace_is_unverified():
    ws = Workspace(
        {"pkg/mod.py": "x = 1\n" + "#" * 1_000_001}, "pkg/mod.py", "tests/t.py"
    )
    a, _ = agent([fix(code="x = 2\n", tests="")])
    out = await a.fix_in_workspace(ws, "x")
    assert out.unverified_reason == "too_large" and a._sandbox.calls == 0


async def test_the_sbx_provider_is_unverified_without_running():
    sandbox = LocalSandbox()
    sandbox.provider = "sbx"
    a, _ = agent([fix()], sandbox)
    out = await a.fix_in_workspace(WS, "quad")
    assert out.unverified_reason == "sandbox_unavailable" and sandbox.calls == 0


async def test_a_package_init_is_not_needed():
    # pkg/__init__.py is never staged: the namespace package still imports.
    a, _ = agent([fix()])
    out = await a.fix_in_workspace(WS, "quad")
    assert out.verified and "pkg/__init__.py" not in WS.files


async def test_tampering_tests_are_refused_and_never_green():
    evil = "import atexit\n\n\ndef test_x():\n    pass\n"
    a, _ = agent([fix(tests=evil), {"code": FIXED, "explanation": "", "changes": []}])
    out = await a.fix_in_workspace(WS, "quad")
    assert not out.success and out.tests_total == 0


async def test_a_secret_in_the_fix_is_withheld():
    leaked = FIXED + 'AWS_SECRET = "AKIAIOSFODNN7EXAMPLE"\n'
    a, _ = agent([fix(code=leaked)])
    out = await a.fix_in_workspace(WS, "quad")
    assert not out.success and out.final_code.startswith("Code withheld")


def test_a_workspace_names_its_target_and_a_new_test_file():
    with pytest.raises(ValueError):
        Workspace({"a.py": "x"}, "b.py", "t.py")
    with pytest.raises(ValueError):
        Workspace({"a.py": "x", "t.py": "y"}, "a.py", "t.py")


async def test_withheld_code_leaves_no_trace_in_the_diff():
    leaked = FIXED + 'AWS_SECRET = "AKIAIOSFODNN7EXAMPLE"\n'
    a, _ = agent([fix(code=leaked)])
    out = await a.fix_in_workspace(WS, "quad")
    assert "AKIAIOSFODNN7EXAMPLE" not in out.diff and out.provenance is None


async def test_an_unverified_secret_is_withheld_too():
    ws = Workspace({"src/a.js": "export const k = 1;\n"}, "src/a.js", "src/a.test.js")
    leaked = 'export const k = "AKIAIOSFODNN7EXAMPLE";\n'
    a, _ = agent([fix(code=leaked, tests="test('k', () => {});\n")])
    out = await a.fix_in_workspace(ws, "k", language="javascript")
    assert not out.success and "AKIAIOSFODNN7EXAMPLE" not in out.diff + out.final_code


def test_a_caller_without_the_class_builds_a_workspace_through_the_agent():
    ws = CodingAgent().workspace(
        files={"pkg/mod.py": BUGGY},
        target="pkg/mod.py",
        test_path="t.py",
        roots=["", "src"],
    )
    assert isinstance(ws, Workspace) and ws.roots == ("", "src")


async def test_a_failed_import_probe_is_unverified_without_a_loop():
    ws = Workspace(
        {"pkg/util.py": "import atexit\n", "pkg/mod.py": "from pkg.util import x\n"},
        "pkg/mod.py",
        "tests/t.py",
    )
    a, llm = agent(
        [
            fix(
                code="from pkg.util import x\n\ny = 1\n",
                tests="def test_y():\n    pass\n",
            )
        ]
    )
    out = await a.fix_in_workspace(ws, "y missing")
    assert not out.verified and out.unverified_reason == "probe_failed"
    assert len(llm.prompts) == 1
