"""
Coding Agent.

An autonomous agent for code generation, debugging, and testing.
Implements an auto-debug loop:
1. Generate/Execute code
2. Analyze errors
3. Fix issues
4. Retry until success

Uses the Sandbox Service for secure code execution.

Usage:
    from core.agents.coding import CodingAgent

    agent = CodingAgent()
    result = await agent.fix_code(buggy_code, error_message)
    result = await agent.generate_tests(code, requirements)
"""

from __future__ import annotations

from typing import Any

from core.observability.logging import get_logger

from .prompts import (
    SYSTEM_PROMPT,
    get_explain_prompt,
    get_fix_prompt,
    get_generate_prompt,
    get_refactor_prompt,
    get_test_prompt,
)
from .types import CodeExecutionResult, CodeLanguage, CodingResult

logger = get_logger(__name__)


class CodingAgent:
    """
    Autonomous coding agent with auto-debug capabilities.

    Features:
    - Auto-fix: Execute → Error → Analyze → Fix → Retry loop
    - Test generation: Generate unit tests from code
    - Code explanation: Explain what code does
    - Refactoring: Improve code quality
    """

    def __init__(
        self,
        max_fix_attempts: int = 5,
        execution_timeout: int = 30,
        language: CodeLanguage = CodeLanguage.PYTHON,
    ) -> None:
        """
        Initialize coding agent.

        Args:
            max_fix_attempts: Maximum attempts to fix code
            execution_timeout: Timeout for code execution in seconds
            language: Default programming language
        """
        self.max_fix_attempts = max_fix_attempts
        self.execution_timeout = execution_timeout
        self.language = language
        self._sandbox: Any | None = None
        self._llm: Any | None = None

        logger.info(
            "coding_agent_initialized",
            max_attempts=max_fix_attempts,
            timeout=execution_timeout,
            language=language.value,
        )

    async def _get_sandbox(self) -> Any:
        """Get or create sandbox service."""
        if self._sandbox is None:
            try:
                from core.services.sandbox.service import SandboxService

                self._sandbox = SandboxService()
            except ImportError:
                logger.error("sandbox_service_not_available")
                # We strictly require SandboxService for security.
                # No fallback to local execution.
                raise RuntimeError(
                    "SandboxService is not available. Please install the required dependencies."
                ) from None
        return self._sandbox

    async def _get_llm(self) -> Any:
        """Get or create LLM service."""
        if self._llm is None:
            try:
                from core.services.llm.service import LLMService

                self._llm = LLMService()
            except ImportError:
                logger.error("llm_service_not_available")
                # We strictly require LLMService for observability and config management.
                raise RuntimeError(
                    "LLMService is not available. Please ensure core.services.llm is correctly configured."
                ) from None
        return self._llm

    async def _execute_code(self, code: str) -> CodeExecutionResult:
        """Execute code in sandbox."""
        sandbox = await self._get_sandbox()

        # Use sandbox service
        try:
            result = await sandbox.execute(
                code=code, language=self.language.value, timeout=self.execution_timeout
            )
            return CodeExecutionResult(
                success=result.success,
                output=result.stdout or "",
                error=result.stderr or "",
                execution_time_ms=result.execution_time_ms,
            )
        except Exception as e:
            return CodeExecutionResult(success=False, error=str(e))

    async def _ask_llm(self, prompt: str) -> str:
        """Ask LLM for code generation/fixing."""
        llm = await self._get_llm()

        # Use LLM service
        try:
            response = await llm.generate(
                prompt=prompt, system_prompt=SYSTEM_PROMPT, temperature=0.0
            )
            return response.content
        except Exception as e:
            logger.error("llm_generation_error", error=str(e))
            raise

    def _extract_code(self, response: str) -> str:
        """Extract code from LLM response."""
        # Remove markdown code blocks if present
        if "```python" in response:
            start = response.find("```python") + 9
            end = response.find("```", start)
            if end > start:
                return response[start:end].strip()

        if "```" in response:
            start = response.find("```") + 3
            # Skip language identifier if present
            newline = response.find("\n", start)
            if newline > start and newline - start < 15:
                start = newline + 1
            end = response.find("```", start)
            if end > start:
                return response[start:end].strip()

        return response.strip()

    # -------------------------------------------------------------------------
    # Main Methods
    # -------------------------------------------------------------------------

    async def fix_code(
        self, code: str, error_message: str, context: str = ""
    ) -> CodingResult:
        """
        Automatically repair code using an iterative self-correction loop.

        Sends the failing code and error message to the LLM, extracts
        the proposed fix, and validates it within a secure sandbox
        environment. Retries until success or max attempts reached.

        Args:
            code: The source code exhibiting errors.
            error_message: The stack trace or error output from execution.
            context: Optional domain context or requirements to guide the repair.

        Returns:
            CodingResult: The outcome of the fix attempt, including
                         iterations and final status.
        """
        logger.info(
            "coding_fix_start",
            code_length=len(code),
            error_preview=error_message[:100],
        )

        current_code = code
        current_error = error_message
        execution_results: list[CodeExecutionResult] = []

        for attempt in range(1, self.max_fix_attempts + 1):
            # Ask LLM to fix
            prompt = get_fix_prompt(
                self.language.value, current_code, current_error, context
            )

            try:
                response = await self._ask_llm(prompt)
                fixed_code = self._extract_code(response)

                logger.info(
                    "coding_fix_attempt",
                    attempt=attempt,
                    code_changed=fixed_code != current_code,
                )

                # Execute to verify
                result = await self._execute_code(fixed_code)
                execution_results.append(result)

                if result.success:
                    # Success!
                    return CodingResult(
                        success=True,
                        original_code=code,
                        final_code=fixed_code,
                        iterations=attempt,
                        explanation=f"Fixed after {attempt} attempt(s)",
                        execution_results=execution_results,
                    )

                # Still has errors, continue loop
                current_code = fixed_code
                current_error = result.error

            except Exception as e:
                logger.error("coding_fix_error", attempt=attempt, error=str(e))
                return CodingResult(
                    success=False,
                    original_code=code,
                    final_code=current_code,
                    iterations=attempt,
                    error=str(e),
                    execution_results=execution_results,
                )

        # Max attempts reached
        return CodingResult(
            success=False,
            original_code=code,
            final_code=current_code,
            iterations=self.max_fix_attempts,
            error=f"Could not fix after {self.max_fix_attempts} attempts. Last error: {current_error}",
            execution_results=execution_results,
        )

    async def generate_code(
        self, description: str, examples: list[str] | None = None
    ) -> CodingResult:
        """
        Synthesize new source code from natural language requirements.

        Performs a syntax check by executing the generated code in
        the sandbox before returning the result.

        Args:
            description: Detailed explanation of the desired functionality.
            examples: Optional list of demonstrating input/output pairs.

        Returns:
            CodingResult: The generated code and its validation status.
        """
        logger.info("coding_generate_start", description=description[:100])

        prompt = get_generate_prompt(self.language.value, description, examples)

        try:
            response = await self._ask_llm(prompt)
            generated_code = self._extract_code(response)

            # Verify it at least parses
            result = await self._execute_code(f"# Syntax check\n{generated_code}\npass")

            return CodingResult(
                success=result.success,
                original_code="",
                final_code=generated_code,
                iterations=1,
                error=result.error if not result.success else None,
                execution_results=[result],
            )

        except Exception as e:
            logger.error("coding_generate_error", error=str(e))
            return CodingResult(
                success=False, original_code="", final_code="", error=str(e)
            )

    async def generate_tests(
        self, code: str, test_framework: str = "pytest"
    ) -> CodingResult:
        """
        Generate unit tests for code.

        Args:
            code: The code to test
            test_framework: Test framework to use (pytest, unittest)

        Returns:
            CodingResult with test code
        """
        logger.info("coding_tests_start", code_length=len(code))

        prompt = get_test_prompt(self.language.value, code, test_framework)

        try:
            response = await self._ask_llm(prompt)
            test_code = self._extract_code(response)

            return CodingResult(
                success=True,
                original_code=code,
                final_code=test_code,
                iterations=1,
                explanation=f"Generated {test_framework} tests",
            )

        except Exception as e:
            logger.error("coding_tests_error", error=str(e))
            return CodingResult(
                success=False, original_code=code, final_code="", error=str(e)
            )

    async def explain_code(self, code: str) -> str:
        """
        Explain what code does.

        Args:
            code: The code to explain

        Returns:
            Explanation string
        """
        prompt = get_explain_prompt(self.language.value, code)

        try:
            return await self._ask_llm(prompt)
        except Exception as e:
            return f"Error explaining code: {e}"

    async def refactor_code(self, code: str, goals: str = "") -> CodingResult:
        """
        Refactor code for better quality.

        Args:
            code: The code to refactor
            goals: Specific refactoring goals

        Returns:
            CodingResult with refactored code
        """
        prompt = get_refactor_prompt(self.language.value, code, goals)

        try:
            response = await self._ask_llm(prompt)
            refactored = self._extract_code(response)

            # Verify it works
            result = await self._execute_code(refactored)

            return CodingResult(
                success=result.success,
                original_code=code,
                final_code=refactored,
                iterations=1,
                explanation=f"Refactored with goals: {goals or 'default'}",
                execution_results=[result],
            )

        except Exception as e:
            logger.error("coding_refactor_error", error=str(e))
            return CodingResult(
                success=False, original_code=code, final_code=code, error=str(e)
            )
