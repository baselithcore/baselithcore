"""
Sandbox Service.

Provides isolated environments for secure code execution.
"""

from core.observability.logging import get_logger
import time
from dataclasses import dataclass
from typing import Optional, Any

try:
    from docker.models.containers import Container
except ImportError:
    Container = Any  # type: ignore

from .docker_factory import DockerFactory

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
    Executes code in isolated Docker containers.
    """

    def __init__(self, docker_factory: Optional[DockerFactory] = None):
        """
        Initialize the Sandbox Service.

        Args:
            docker_factory: Provider for isolated container environments.
        """
        self.docker_factory = docker_factory or DockerFactory()

    async def execute_code_async(
        self,
        code: str,
        language: str = "python",
        timeout: int = 30,
        mounts: Optional[dict[str, str]] = None,
    ) -> ExecutionResult:
        """
        Execute code asynchronously in a sandbox container.

        Args:
            code: Code to execute.
            language: Language runtime.
            timeout: Execution timeout in seconds.
            mounts: Dictionary of host_path:container_path mapping for volumes.

        Returns:
            ExecutionResult
        """
        import asyncio

        # Guard import for testing environments
        try:
            from docker.types import Mount
        except ImportError:

            def Mount(target, source, type="bind", **kwargs):
                """
                Mock Mount object for environments where docker-py is missing.

                Args:
                    target: Path inside container.
                    source: Path on host.
                    type: Mount type (default 'bind').
                    **kwargs: Passed to dictionary.
                """
                return {"Target": target, "Source": source, "Type": type, **kwargs}

        loop = asyncio.get_running_loop()

        # Prepare mounts
        # Prepare mounts
        docker_mounts = []
        if mounts:
            for source, target in mounts.items():
                docker_mounts.append(Mount(target=target, source=source, type="bind"))

        # Ensure image first (async)
        await self.docker_factory.ensure_image()

        def _blocking_run():
            """
            Synchronous blocking logic for Docker container execution.

            Returns:
                ExecutionResult with logs and timing metrics.
            """
            start_time = time.time()
            container: Optional[Container] = None
            try:
                # Image ensured above

                if language.lower() == "python":
                    cmd = ["python", "-c", code]
                else:
                    return ExecutionResult(
                        stdout="",
                        stderr=f"Unsupported language: {language}",
                        exit_code=1,
                        execution_time=0,
                    )

                container = self.docker_factory.client.containers.run(
                    self.docker_factory.base_image,
                    command=cmd,
                    detach=True,
                    network_mode="none",
                    mem_limit="128m",
                    cpu_period=100000,
                    cpu_quota=50000,
                    mounts=docker_mounts,
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
                logger.error(f"Sandbox execution failed: {e}")
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
        self, code: str, language: str = "python", timeout: int = 30
    ) -> ExecutionResult:
        """
        Sync wrapper for fallback.
        """
        import asyncio

        # This is a bit hacky for sync context, mainly used for legacy tests
        # or if we are already in a thread.
        # Ideally, use execute_code_async.
        return asyncio.run(self.execute_code_async(code, language, timeout))
