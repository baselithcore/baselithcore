"""
Sandbox Service.

Provides isolated environments for secure code execution.
"""

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from core.observability.logging import get_logger

try:
    from docker.models.containers import Container
except ImportError:
    Container = Any  # type: ignore

from core.config.sandbox import SandboxConfig, SandboxProvider, get_sandbox_config

from .docker_factory import DockerFactory
from .policy import build_sandbox_runtime_kwargs
from .sbx_factory import SbxFactory

logger = get_logger(__name__)


@dataclass
class ExecutionResult:
    """Result of code execution in sandbox.

    Attributes:
        stdout: Captured standard output.
        stderr: Captured standard error.
        exit_code: Process exit code (124 on timeout).
        execution_time: Wall-clock duration of the execution in seconds.
        compute_seconds: Metered wall-clock compute time in seconds
            (0.0 when execution never started, e.g. static-analysis
            rejection).
        cost_usd: Metered cost in USD — ``compute_seconds`` multiplied by
            ``SandboxConfig.cost_per_compute_second`` (0.0 when the rate
            is 0).
    """

    stdout: str
    stderr: str
    exit_code: int
    execution_time: float
    compute_seconds: float = 0.0
    cost_usd: float = 0.0


class SandboxService:
    """
    Executes code in isolated Docker or MicroVM (sbx) containers.
    """

    def __init__(
        self,
        docker_factory: DockerFactory | None = None,
        sbx_factory: SbxFactory | None = None,
        provider: SandboxProvider | None = None,
    ):
        """
        Initialize the Sandbox Service.

        Args:
            docker_factory: Provider for isolated container environments.
            sbx_factory: Provider for microVM-based sandboxes (sbx).
            provider: Explicitly set the provider (overrides config).
        """
        config = get_sandbox_config()
        self.provider = provider or config.provider
        self.docker_factory = docker_factory or DockerFactory(base_image=config.image)
        self.sbx_factory = sbx_factory or SbxFactory(
            sbx_path=config.sbx_path, profile=config.sbx_profile
        )

    async def execute_code_async(
        self,
        code: str,
        language: str = "python",
        timeout: int | None = None,
        mounts: dict[str, str] | None = None,
        envs: dict[str, str] | None = None,
        budget: Any | None = None,
    ) -> ExecutionResult:
        """
        Execute code asynchronously in a sandbox environment.

        Args:
            code: Code to execute.
            language: Language runtime.
            timeout: Execution timeout in seconds (optional, defaults to config).
            mounts: Dictionary of host_path:container_path mapping for volumes.
            envs: Environment variables for the sandbox.
            budget: Optional LoopBudget-shaped object with a
                ``charge(cost_usd)`` method. The metered execution cost is
                charged against it after each execution;
                ``BudgetExceededError`` propagates to the caller.

        Returns:
            ExecutionResult with ``compute_seconds``/``cost_usd`` populated
            from wall-clock time and ``SandboxConfig.cost_per_compute_second``.
        """

        config = get_sandbox_config()
        timeout = timeout or config.timeout

        # Static analysis before any container spin-up (rejections carry
        # zero compute_seconds/cost_usd and never touch the budget).
        rejection = self._pre_screen(code, language, config)
        if rejection is not None:
            return rejection

        if self.provider == "sbx":
            result = await self._execute_sbx_async(
                code, language, timeout, mounts, envs
            )
        else:
            result = await self._execute_docker_async(
                code, language, timeout, mounts, envs
            )
        return self._meter_and_charge(result, config, budget)

    def _pre_screen(
        self, code: str, language: str, config: SandboxConfig
    ) -> ExecutionResult | None:
        """Static-analysis pre-screen shared by the blocking and stream paths.

        Syntax errors are rejected outright; flagged imports warn or block
        per config. Runs before any container spin-up.

        Returns:
            A rejection ExecutionResult, or None when execution may proceed.
        """
        if not (config.static_analysis and language.lower() == "python"):
            return None

        from core.services.sandbox.static_analysis import (
            analyze_python,
            parse_denied_imports,
        )

        report = analyze_python(
            code, parse_denied_imports(config.static_analysis_denied_imports)
        )
        if not report.parse_ok:
            return ExecutionResult(
                stdout="",
                stderr=f"Static analysis: syntax error — {report.syntax_error}",
                exit_code=1,
                execution_time=0.0,
            )
        if report.flagged_imports:
            if config.static_analysis_mode == "block":
                return ExecutionResult(
                    stdout="",
                    stderr=(
                        "Static analysis: blocked imports "
                        f"{report.flagged_imports} (SANDBOX_STATIC_ANALYSIS_MODE"
                        "=block)"
                    ),
                    exit_code=1,
                    execution_time=0.0,
                )
            logger.warning(
                "sandbox_static_analysis_flagged imports=%s (mode=warn)",
                report.flagged_imports,
            )
        return None

    @staticmethod
    def _meter_and_charge(
        result: ExecutionResult, config: SandboxConfig, budget: Any | None
    ) -> ExecutionResult:
        """Fill metering fields from wall-clock time and charge the budget.

        The timeout path is metered too: its result carries
        ``execution_time == timeout``, so the timed-out wall-clock is
        charged like any other execution.

        Raises:
            BudgetExceededError: When the charge pushes ``budget`` over its
                USD cap (propagated from ``budget.charge``).
        """
        result.compute_seconds = float(result.execution_time)
        result.cost_usd = result.compute_seconds * config.cost_per_compute_second
        if budget is not None and result.cost_usd > 0:
            budget.charge(result.cost_usd)
        return result

    async def execute_code_stream(
        self,
        code: str,
        language: str = "python",
        timeout: int | None = None,
        mounts: dict[str, str] | None = None,
        envs: dict[str, str] | None = None,
        budget: Any | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Execute code in the sandbox, streaming output incrementally.

        Yields frames ``{"stream": "stdout"|"stderr", "data": str}`` as
        output is produced, terminated by a final
        ``{"stream": "exit", "exit_code": int, "compute_seconds": float,
        "cost_usd": float}`` frame. Honors the same timeout and
        static-analysis pre-screen as :meth:`execute_code_async`; on
        timeout the container is killed and the exit frame reports
        ``exit_code == -1``. The sbx provider has no streaming primitive,
        so it degrades to run-to-completion with single stdout/stderr
        frames.

        Args:
            code: Code to execute.
            language: Language runtime.
            timeout: Execution timeout in seconds (optional, defaults to config).
            mounts: Dictionary of host_path:container_path mapping for volumes.
            envs: Environment variables for the sandbox.
            budget: Optional LoopBudget-shaped object with a
                ``charge(cost_usd)`` method, charged just before the exit
                frame is yielded so ``BudgetExceededError`` propagates
                through the generator.

        Yields:
            Stream frames as described above.
        """
        config = get_sandbox_config()
        timeout = timeout or config.timeout
        rate = config.cost_per_compute_second

        rejection = self._pre_screen(code, language, config)
        if rejection is not None:
            yield {"stream": "stderr", "data": rejection.stderr}
            yield {
                "stream": "exit",
                "exit_code": rejection.exit_code,
                "compute_seconds": 0.0,
                "cost_usd": 0.0,
            }
            return

        from .streaming import stream_docker_execution, stream_sbx_execution

        if self.provider == "sbx":
            frames = stream_sbx_execution(
                self, code, language, timeout, mounts, envs, rate
            )
        else:
            frames = stream_docker_execution(
                self, code, language, timeout, mounts, envs, rate
            )

        async for frame in frames:
            if frame.get("stream") == "exit" and budget is not None:
                cost = float(frame.get("cost_usd", 0.0))
                if cost > 0:
                    budget.charge(cost)
            yield frame

    async def _execute_sbx_async(
        self,
        code: str,
        language: str,
        timeout: int,
        mounts: dict[str, str] | None,
        envs: dict[str, str] | None,
    ) -> ExecutionResult:
        """Internal sbx execution path."""
        start_time = time.time()
        try:
            # Ensure sbx is available
            await self.sbx_factory.ensure_available()

            # Prepare command
            if language.lower() == "python":
                command = ["python3", "-c", code]
            elif language.lower() == "sh" or language.lower() == "bash":
                command = ["sh", "-c", code]
            else:
                return ExecutionResult(
                    stdout="",
                    stderr=f"Language '{language}' not supported by sbx provider",
                    exit_code=1,
                    execution_time=0,
                )

            stdout, stderr, exit_code = await self.sbx_factory.client.run(
                command=command,
                image=get_sandbox_config().image,
                envs=envs,
                mounts=mounts,
                timeout=timeout,
            )

            return ExecutionResult(
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                execution_time=time.time() - start_time,
            )

        except Exception as e:
            logger.error(f"Sbx execution failed: {e}")
            return ExecutionResult(
                stdout="",
                stderr=str(e),
                exit_code=1,
                execution_time=time.time() - start_time,
            )

    async def _execute_docker_async(
        self,
        code: str,
        language: str,
        timeout: int,
        mounts: dict[str, str] | None,
        envs: dict[str, str] | None,
    ) -> ExecutionResult:
        """Internal Docker execution path (Legacy)."""
        import asyncio

        # Guard import for testing environments
        try:
            from docker.types import Mount
        except ImportError:

            def Mount(target, source, type="bind", **kwargs):
                """Mock Mount object for environments where docker-py is missing."""
                return {"Target": target, "Source": source, "Type": type, **kwargs}

        loop = asyncio.get_running_loop()

        # Prepare mounts
        docker_mounts = []
        if mounts:
            for source, target in mounts.items():
                docker_mounts.append(Mount(target=target, source=source, type="bind"))

        # Ensure image first (async)
        await self.docker_factory.ensure_image()

        def _blocking_run():
            """Synchronous blocking logic for Docker container execution."""
            start_time = time.time()
            container: Container | None = None
            try:
                if language.lower() == "python":
                    cmd = ["python", "-c", code]
                else:
                    return ExecutionResult(
                        stdout="",
                        stderr=f"Unsupported language for Docker provider: {language}",
                        exit_code=1,
                        execution_time=0,
                    )

                container = self.docker_factory.client.containers.run(
                    self.docker_factory.base_image,
                    command=cmd,
                    detach=True,
                    mounts=docker_mounts,
                    environment=envs or {},
                    **build_sandbox_runtime_kwargs(),
                )

                try:
                    result = container.wait(timeout=timeout)
                    exit_code = result.get("StatusCode", 1)
                except Exception:
                    container.kill()
                    return ExecutionResult(
                        stdout="",
                        stderr="Execution timed out",
                        exit_code=124,
                        execution_time=timeout,
                    )

                stdout = (
                    container.logs(stdout=True, stderr=False).decode("utf-8").strip()
                )
                stderr = (
                    container.logs(stdout=False, stderr=True).decode("utf-8").strip()
                )

                return ExecutionResult(
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=exit_code,
                    execution_time=time.time() - start_time,
                )

            except Exception as e:
                logger.error(f"Sandbox (Docker) execution failed: {e}")
                return ExecutionResult(
                    stdout="",
                    stderr=str(e),
                    exit_code=1,
                    execution_time=time.time() - start_time,
                )
            finally:
                if container:
                    try:
                        container.remove(force=True)
                    except Exception as e:
                        logger.warning(f"Failed to remove sandbox container: {e}")

        return await loop.run_in_executor(None, _blocking_run)

    def execute_code(
        self, code: str, language: str = "python", timeout: int | None = None
    ) -> ExecutionResult:
        """Sync wrapper for fallback."""
        import asyncio

        return asyncio.run(self.execute_code_async(code, language, timeout))
