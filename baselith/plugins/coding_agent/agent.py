"""
Coding Agent.

Writes, fixes, tests, explains and refactors code, and proves what it returns:

1. The model answers with typed JSON (:mod:`.schemas`), code and pytest tests together.
2. The tests run against the code in the core sandbox (:mod:`.harness`).
3. ``baselith.core.loops.EngineeredLoop`` drives the retries: each failed run becomes a lesson for the
   next attempt, and a run that keeps hitting the same failure stops early (:mod:`.loop`).
4. The deterministic security review and an AI-generated provenance tag are applied last.

Everything a caller passes in is treated as data (:mod:`.prompts`). Only Python executes;
any language can be explained.

Usage:
    from baselith.plugins.coding_agent import CodingAgent

    agent = CodingAgent()
    result = await agent.fix_code(buggy_code, error_message)
    result.success, result.tests_passed, result.tests_total, result.test_code
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel

from baselith.core.guardrails.code_review import review_code
from baselith.core.observability.agent_spans import agent_span
from baselith.core.observability.logging import get_logger

from . import flows
from .compliance import mark_generated
from .harness import TestReport, build_test_script, new_run_key, parse_report, screen
from .llm import ask_typed
from .prompts import TEXT_SYSTEM_PROMPT, explain_prompt
from .types import CodeExecutionResult, CodeLanguage, CodingResult
from .workspace import Workspace, WorkspaceResult
from .workspace_agent import run_workspace_tests, workspace_result

logger = get_logger(__name__)

M = TypeVar("M", bound=BaseModel)
PLUGIN = "coding-agent"
_NOT_RUNNABLE = (
    "only Python runs in the sandbox; code in other languages can only be explained"
)


@dataclass
class _Call:
    """What one task's flow sees of the agent: the task language, typed asks, test runs."""

    agent: CodingAgent
    language: str
    max_attempts: int

    async def ask(self, prompt: str, model: type[M]) -> M:
        return await self.agent._ask(prompt, model)

    async def run_tests(self, code: str, tests: str) -> TestReport:
        return await self.agent._run_tests(code, tests)


class CodingAgent:
    """Coding agent whose answers are verified by tests run in the sandbox."""

    def __init__(
        self,
        max_fix_attempts: int = 5,
        execution_timeout: int = 30,
        language: CodeLanguage = CodeLanguage.PYTHON,
    ) -> None:
        """
        Initialize coding agent.

        Args:
            max_fix_attempts: Most attempts of one verified loop (fix, write, refactor).
            execution_timeout: Seconds one sandbox run (code plus tests) may take.
            language: The language used when a call names none.
        """
        self.max_fix_attempts = max_fix_attempts
        self.execution_timeout = execution_timeout
        self.language = language
        self._sandbox: Any | None = None
        # Optional injected LLM override. A caller with its own scoped LLM service
        # may set ``agent._llm``; when unset, ``_get_llm`` uses the central funnel.
        self._llm: Any | None = None

    async def _get_sandbox(self) -> Any:
        """Get or create sandbox service."""
        if self._sandbox is None:
            try:
                from baselith.core.services.sandbox.service import SandboxService

                self._sandbox = SandboxService()
            except ImportError:
                logger.error("sandbox_service_not_available")
                raise RuntimeError(
                    "SandboxService is not available. Please install the required dependencies."
                ) from None
        return self._sandbox

    async def _get_llm(self) -> Any:
        """The injected client, or the central policy-aware funnel resolved per call."""
        if self._llm is not None:
            return self._llm
        try:
            from baselith.core.services.llm import get_llm_service

            return get_llm_service()
        except ImportError:
            logger.error("llm_service_not_available")
            raise RuntimeError(
                "LLMService is not available. Please ensure baselith.core.services.llm is correctly configured."
            ) from None

    async def _ask_llm(self, prompt: str) -> str:
        """One plain-text answer (explanations, and the typed path's fallback)."""
        llm = await self._get_llm()
        try:
            response = await llm.generate(
                prompt=prompt, system_prompt=TEXT_SYSTEM_PROMPT, temperature=0.0
            )
            # The central funnel returns an ``LLMResult`` (``.text``); an injected
            # client may still answer with ``.content``.
            for attr in ("text", "content"):
                value = getattr(response, attr, None)
                if isinstance(value, str):
                    return value
            return ""
        except Exception as exc:
            logger.error("llm_generation_error", error_type=type(exc).__name__)
            raise

    async def _ask(self, prompt: str, model: type[M]) -> M:
        """A validated ``model`` answer (schema-bound, plain-text fallback)."""
        return await ask_typed(
            await self._get_llm(), prompt, model, ask_text=self._ask_llm
        )

    async def _execute_code(
        self, code: str, *, mounts: dict[str, str] | None = None
    ) -> CodeExecutionResult:
        """Run ``code`` (a Python script) in the sandbox; ``mounts`` are bound read-only."""
        sandbox = await self._get_sandbox()
        try:
            if mounts:
                result = await sandbox.execute_code_async(
                    code,
                    language="python",
                    timeout=self.execution_timeout,
                    mounts=mounts,
                    mounts_read_only=True,
                )
            else:
                result = await sandbox.execute_code_async(
                    code, language="python", timeout=self.execution_timeout
                )
        except Exception as exc:
            # Sandbox infrastructure failure (e.g. Docker unavailable): a failed result the
            # loop reports as evidence, logged so a broken integration never hides.
            logger.error(
                "sandbox_execution_error", error=str(exc), error_type=type(exc).__name__
            )
            return CodeExecutionResult(success=False, error=str(exc))
        return CodeExecutionResult(
            success=result.exit_code == 0,
            output=result.stdout or "",
            error=result.stderr or "",
            execution_time_ms=int(result.execution_time * 1000),
            exit_code=result.exit_code,
        )

    async def _run_tests(self, code: str, tests: str) -> TestReport:
        """Run ``tests`` against ``code`` in the sandbox and read the report back."""
        refused = screen(code, tests)
        if refused is not None:
            return TestReport(problem=f"refused before running: {refused}")
        key = new_run_key()
        run = await self._execute_code(build_test_script(code, tests, key=key))
        exit_code = (
            run.exit_code if run.exit_code is not None else (0 if run.success else 1)
        )
        return parse_report(
            run.output, run.error, exit_code, run.execution_time_ms / 1000, key=key
        )

    def _extract_code(self, response: str) -> str:
        """The first fenced block of ``response``, or the whole text (pre-1.1 helper)."""
        start = response.find("```")
        if start >= 0:
            newline = response.find("\n", start)
            end = response.find("```", newline + 1)
            if newline > start and end > newline:
                return response[newline + 1 : end].strip()
        return response.strip()

    def _apply_security_review(self, result: CodingResult) -> CodingResult:
        """Run the deterministic security review over ``final_code``.

        A ``high``-severity review (leaked credential, eval/exec on dynamic input) replaces the
        code with a refusal note listing the findings: the agent never ships a secret. Lower
        severities ride along on the result without blocking.
        """
        if not result.final_code:
            return result
        review = review_code(result.final_code)
        result.review = review
        if review.verdict == "approved":
            return result
        if review.severity == "high":
            findings = "\n".join(
                f"- [{c.severity}]"
                + (f" line {c.line}:" if c.line is not None else "")
                + f" {c.message}"
                for c in review.comments
            )
            result.final_code = (
                "Code withheld by deterministic security review "
                "(severity: high). The generated code must not ship a "
                f"credential or equivalent finding.\nFindings:\n{findings}"
            )
            result.success = False
            result.error = (
                result.error or "security review flagged high-severity findings"
            )
            logger.warning(
                "coding_review_blocked",
                severity=review.severity,
                findings=len(review.comments),
            )
        else:
            logger.warning(
                "coding_review_flagged",
                severity=review.severity,
                findings=len(review.comments),
            )
        return result

    def _review_tests(self, result: CodingResult) -> CodingResult:
        """The same review over the returned tests: a test file never ships a secret either."""
        if not result.test_code or result.test_code == result.final_code:
            return result
        review = review_code(result.test_code)
        if review.verdict == "flagged" and review.severity == "high":
            result.test_code = (
                "Tests withheld by deterministic security review (severity: high): "
                + "; ".join(c.message for c in review.comments)
            )
            result.success = False
            result.error = (
                result.error or "security review flagged the tests (high severity)"
            )
            logger.warning("coding_review_blocked_tests", findings=len(review.comments))
        return result

    def _resolve(self, language: str | CodeLanguage | None) -> str:
        if language is None:
            return self.language.value
        return str(
            language.value if isinstance(language, CodeLanguage) else language
        ).lower()

    async def _verified(
        self,
        task: str,
        language: str | CodeLanguage | None,
        original: str,
        run: Callable[[_Call], Awaitable[CodingResult]],
    ) -> CodingResult:
        """Run one verified task: span, language check, flow, review, provenance."""
        lang = self._resolve(language)
        with agent_span(
            f"coding_agent.{task}",
            plugin=PLUGIN,
            attributes={"coding.task": task, "coding.language": lang},
        ) as span:
            if lang != CodeLanguage.PYTHON.value:
                return CodingResult(
                    success=False,
                    original_code=original,
                    final_code="",
                    error=_NOT_RUNNABLE,
                )
            try:
                result = await run(_Call(self, lang, self.max_fix_attempts))
            except Exception as exc:
                logger.error(
                    "coding_task_error", task=task, error_type=type(exc).__name__
                )
                result = CodingResult(
                    success=False,
                    original_code=original,
                    final_code="",
                    error=f"the coding agent failed ({type(exc).__name__}: {str(exc)[:200]})",
                    loop_status="failed",
                )
            result = self._review_tests(self._apply_security_review(result))
            generated = result.final_code and result.final_code != result.original_code
            if generated and not result.final_code.startswith("Code withheld"):
                result.provenance = mark_generated(result.final_code)
            outcome = {
                "coding.success": result.success,
                "coding.loop_status": result.loop_status,
                "coding.attempts": result.iterations,
                "coding.tests_passed": result.tests_passed,
                "coding.tests_total": result.tests_total,
                "coding.sandbox_seconds": round(result.sandbox_seconds, 2),
            }
            if span is not None:
                span.set_attributes(outcome)
            logger.info(
                "coding_task_done",
                task=task,
                success=result.success,
                loop_status=result.loop_status,
                attempts=result.iterations,
                tests_passed=result.tests_passed,
                tests_total=result.tests_total,
                sandbox_seconds=round(result.sandbox_seconds, 2),
            )
            return result

    async def fix_code(
        self,
        code: str,
        error_message: str,
        context: str = "",
        *,
        language: str | CodeLanguage | None = None,
    ) -> CodingResult:
        """Fix ``code``: tests that reproduce the bug are written first, then the fix is
        retried until they pass (``tests_reproduce_bug`` says whether they fail on ``code``)."""
        return await self._verified(
            "fix", language, code, lambda k: flows.fix(k, code, error_message, context)
        )

    def workspace(
        self,
        *,
        files: Mapping[str, str],
        target: str,
        test_path: str,
        roots: Sequence[str] = ("",),
    ) -> Workspace:
        """A :class:`Workspace` for :meth:`fix_in_workspace`, for callers that reach the agent
        through the plugin registry and do not import this package."""
        return Workspace(dict(files), target, test_path, tuple(roots))

    async def _run_workspace_tests(
        self, ws: Workspace, code: str, tests: str
    ) -> TestReport:
        """``tests`` against ``ws`` with ``code`` as its target, in the sandbox."""
        return await run_workspace_tests(self, ws, code, tests)

    async def fix_in_workspace(
        self,
        workspace: Workspace,
        problem: str,
        *,
        language: str | CodeLanguage | None = None,
    ) -> WorkspaceResult:
        """Fix ``workspace.target`` and prove it with tests run against the workspace.

        Python runs in the sandbox (``verified``): tests that reproduce the problem are
        written first and frozen, then the fix is retried until they pass. Another language,
        a third-party import the sandbox lacks, an oversize workspace or a sandbox without
        read-only mounts returns an unverified proposal with ``unverified_reason``.
        """
        lang = self._resolve(language)
        with agent_span(
            "coding_agent.fix_workspace",
            plugin=PLUGIN,
            attributes={"coding.task": "fix_workspace", "coding.language": lang},
        ) as span:
            try:
                result = await workspace_result(self, workspace, problem, lang)
            except Exception as exc:
                logger.error(
                    "coding_task_error",
                    task="fix_workspace",
                    error_type=type(exc).__name__,
                )
                result = WorkspaceResult(
                    success=False,
                    original_code=workspace.files[workspace.target],
                    final_code="",
                    error=f"the coding agent failed ({type(exc).__name__}: {str(exc)[:200]})",
                    loop_status="failed",
                    target=workspace.target,
                    test_path=workspace.test_path,
                )
            self._review_tests(self._apply_security_review(result))
            withheld = result.final_code.startswith("Code withheld")
            if withheld:
                # The diff quotes the withheld code: it goes with it.
                result.diff = ""
            generated = result.final_code and result.final_code != result.original_code
            if generated and not withheld:
                result.provenance = mark_generated(result.final_code)
            if span is not None:
                span.set_attributes(
                    {
                        "coding.success": result.success,
                        "coding.verified": result.verified,
                        "coding.unverified_reason": result.unverified_reason,
                        "coding.tests_passed": result.tests_passed,
                        "coding.tests_total": result.tests_total,
                    }
                )
            logger.info(
                "coding_task_done",
                task="fix_workspace",
                success=result.success,
                verified=result.verified,
                reason=result.unverified_reason,
                tests_passed=result.tests_passed,
                tests_total=result.tests_total,
            )
            return result

    async def generate_code(
        self,
        description: str,
        examples: list[str] | None = None,
        *,
        language: str | CodeLanguage | None = None,
    ) -> CodingResult:
        """Write code for ``description`` together with the tests it has to pass."""
        return await self._verified(
            "write", language, "", lambda k: flows.write(k, description, examples)
        )

    async def generate_tests(
        self,
        code: str,
        test_framework: str = "pytest",
        *,
        language: str | CodeLanguage | None = None,
    ) -> CodingResult:
        """Write pytest tests for ``code`` and run them once against it (``final_code`` holds
        the tests; ``unittest`` style runs under pytest too)."""
        return await self._verified(
            "tests", language, code, lambda k: flows.write_tests(k, code)
        )

    async def refactor_code(
        self, code: str, goals: str = "", *, language: str | CodeLanguage | None = None
    ) -> CodingResult:
        """Refactor ``code`` for ``goals``; characterization tests pin today's behaviour first."""
        return await self._verified(
            "refactor", language, code, lambda k: flows.refactor(k, code, goals)
        )

    async def explain_code(
        self, code: str, *, language: str | CodeLanguage | None = None
    ) -> str:
        """Explain ``code`` in plain words (any language; nothing is executed)."""
        lang = self._resolve(language)
        with agent_span(
            "coding_agent.explain",
            plugin=PLUGIN,
            attributes={"coding.task": "explain", "coding.language": lang},
        ):
            try:
                return await self._ask_llm(explain_prompt(lang, code))
            except Exception as exc:
                return f"Error explaining code: {exc}"
