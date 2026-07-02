"""
Sandbox Service.

Provides isolated environments for secure code execution.
"""

import time
from dataclasses import dataclass
from typing import Any

from core.observability.logging import get_logger

try:
    from docker.models.containers import Container
except ImportError:
    Container = Any  # type: ignore

from core.config.sandbox import SandboxProvider, get_sandbox_config

from .docker_factory import DockerFactory
from .policy import build_sandbox_runtime_kwargs
from .sbx_factory import SbxFactory

logger = get_logger(__name__)


@dataclass
class ExecutionResult:
    """Result of code execution in sandbox."""

    stdout: str
    stderr: str
    exit_code: int
    execution_time: float


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
    ) -> ExecutionResult:
        """
        Execute code asynchronously in a sandbox environment.

        Args:
            code: Code to execute.
            language: Language runtime.
            timeout: Execution timeout in seconds (optional, defaults to config).
            mounts: Dictionary of host_path:container_path mapping for volumes.
            envs: Environment variables for the sandbox.

        Returns:
            ExecutionResult
        """

        config = get_sandbox_config()
        timeout = timeout or config.timeout

        if self.provider == "sbx":
            return await self._execute_sbx_async(code, language, timeout, mounts, envs)
        else:
            return await self._execute_docker_async(
                code, language, timeout, mounts, envs
            )

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
