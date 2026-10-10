"""The agent side of a repository fix: stage, run, choose verified or unverified.

Kept out of :mod:`plugins.coding_agent.agent` so each module stays small; every function takes
the :class:`~plugins.coding_agent.agent.CodingAgent` it works for.
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING, Any

from plugins.coding_agent.harness import (
    TestReport,
    new_run_key,
    parse_report,
    screen_texts,
)
from plugins.coding_agent.llm import ask_typed
from plugins.coding_agent.workspace import Workspace, WorkspaceResult
from plugins.coding_agent.workspace_flow import missing_module, unverified, verified
from plugins.coding_agent.workspace_harness import (
    WORK_DIR,
    WORKSPACE_MAX_BYTES,
    build_workspace_script,
    stage,
)
from plugins.coding_agent.workspace_prompts import WORKSPACE_SYSTEM_PROMPT, module_path

if TYPE_CHECKING:
    from plugins.coding_agent.agent import CodingAgent


async def run_workspace_tests(
    agent: CodingAgent, ws: Workspace, code: str, tests: str
) -> TestReport:
    """Run ``tests`` against the workspace with ``code`` as the target, in the sandbox.

    Every file that runs is screened first, context files included: a repository file is code
    under test as much as the fix is.
    """
    refused = screen_texts([*ws.files.values(), code, tests])
    if refused is not None:
        return TestReport(problem=f"refused before running: {refused}")
    try:
        host = stage({**ws.files, ws.target: code, ws.test_path: tests})
    except ValueError as exc:
        return TestReport(problem=str(exc))
    try:
        key = new_run_key()
        run = await agent._execute_code(
            build_workspace_script(ws.test_path, ws.roots, key=key),
            mounts={host: WORK_DIR},
        )
    finally:
        shutil.rmtree(host, ignore_errors=True)
    exit_code = (
        run.exit_code if run.exit_code is not None else (0 if run.success else 1)
    )
    return parse_report(
        run.output, run.error, exit_code, run.execution_time_ms / 1000, key=key
    )


async def ask_workspace(agent: CodingAgent, prompt: str, model: Any) -> Any:
    """A typed answer under the workspace system prompt."""
    return await ask_typed(
        await agent._get_llm(),
        prompt,
        model,
        ask_text=agent._ask_llm,
        system=WORKSPACE_SYSTEM_PROMPT,
    )


async def workspace_result(
    agent: CodingAgent, ws: Workspace, problem: str, lang: str
) -> WorkspaceResult:
    """Verified when the workspace can run in the sandbox, unverified (with why) otherwise."""

    async def ask(prompt: str, model: Any) -> Any:
        return await ask_workspace(agent, prompt, model)

    if lang != "python":
        return await unverified(ask, ws, problem, lang, "language")
    if sum(len(t.encode("utf-8")) for t in ws.files.values()) > WORKSPACE_MAX_BYTES:
        return await unverified(ask, ws, problem, lang, "too_large")
    if getattr(await agent._get_sandbox(), "provider", "docker") == "sbx":
        return await unverified(ask, ws, problem, lang, "sandbox_unavailable")
    probe = await run_workspace_tests(
        agent,
        ws,
        ws.files[ws.target],
        f"import {module_path(ws.target, ws.roots)}\n\n\ndef test_imports():\n    pass\n",
    )
    if missing_module(probe, ws) is not None:
        return await unverified(ask, ws, problem, lang, "missing_dependency")
    if not probe.green:
        # The target does not import in the sandbox as it is (a screened context file, a
        # local import that fails, a file that is not a module): no test run can pass.
        return await unverified(ask, ws, problem, lang, "probe_failed")

    async def run_tests(code: str, tests: str) -> TestReport:
        return await run_workspace_tests(agent, ws, code, tests)

    return await verified(ask, run_tests, ws, problem, lang, agent.max_fix_attempts)


__all__ = ["ask_workspace", "run_workspace_tests", "workspace_result"]
