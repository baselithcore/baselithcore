"""
Streaming sandbox execution backends.

Implements the incremental-output side of
:meth:`~core.services.sandbox.service.SandboxService.execute_code_stream`.
Frames are plain dicts: ``{"stream": "stdout"|"stderr", "data": str}`` for
output, terminated by ``{"stream": "exit", "exit_code": int,
"compute_seconds": float, "cost_usd": float}``.

The Docker backend attaches to the container's demuxed output stream from a
worker thread (mirroring the service's to-thread pattern for blocking
docker-py calls) and forwards chunks through an ``asyncio.Queue``. The sbx
CLI has no streaming primitive, so its backend degrades to
run-to-completion and emits the collected output as single frames.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger

from .policy import build_sandbox_runtime_kwargs

if TYPE_CHECKING:
    from .service import SandboxService

logger = get_logger(__name__)

StreamFrame = dict[str, Any]


def _exit_frame(exit_code: int, compute_seconds: float, rate: float) -> StreamFrame:
    """Build the terminal exit frame with metering fields."""
    return {
        "stream": "exit",
        "exit_code": exit_code,
        "compute_seconds": compute_seconds,
        "cost_usd": compute_seconds * rate,
    }


def _build_docker_mounts(mounts: dict[str, str] | None) -> list[Any]:
    """Translate host:container path mappings into docker Mount objects."""
    try:
        from docker.types import Mount
    except ImportError:

        def Mount(target: str, source: str, type: str = "bind", **kwargs: Any) -> Any:
            """Mock Mount object for environments where docker-py is missing."""
            return {"Target": target, "Source": source, "Type": type, **kwargs}

    if not mounts:
        return []
    return [
        Mount(target=target, source=source, type="bind")
        for source, target in mounts.items()
    ]


async def stream_docker_execution(
    service: SandboxService,
    code: str,
    language: str,
    timeout: int,
    mounts: dict[str, str] | None,
    envs: dict[str, str] | None,
    rate: float,
) -> AsyncIterator[StreamFrame]:
    """Stream a Docker sandbox execution incrementally.

    Runs the blocking docker-py attach loop in a worker thread and forwards
    demuxed stdout/stderr chunks as frames. On timeout the container is
    killed and the exit frame reports ``exit_code == -1`` with
    ``compute_seconds == timeout``.

    Args:
        service: The owning sandbox service (provides the docker factory).
        code: Code to execute.
        language: Language runtime (only ``python`` is supported).
        timeout: Execution timeout in seconds.
        mounts: host_path:container_path volume mappings.
        envs: Environment variables for the sandbox.
        rate: USD per compute second (``cost_per_compute_second``).

    Yields:
        Output frames followed by a terminal exit frame.
    """
    if language.lower() != "python":
        yield {
            "stream": "stderr",
            "data": f"Unsupported language for Docker provider: {language}",
        }
        yield _exit_frame(1, 0.0, rate)
        return

    await service.docker_factory.ensure_image()

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[StreamFrame | None] = asyncio.Queue()
    docker_mounts = _build_docker_mounts(mounts)
    state: dict[str, Any] = {"container": None}
    start_time = time.time()

    def _put(frame: StreamFrame | None) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, frame)

    def _reader() -> None:
        """Blocking attach loop executed in a worker thread."""
        container: Any | None = None
        try:
            container = service.docker_factory.client.containers.run(
                service.docker_factory.base_image,
                command=["python", "-c", code],
                detach=True,
                mounts=docker_mounts,
                environment=envs or {},
                **build_sandbox_runtime_kwargs(),
            )
            state["container"] = container
            stream = container.attach(
                stdout=True, stderr=True, stream=True, logs=True, demux=True
            )
            for out_chunk, err_chunk in stream:
                if out_chunk:
                    _put(
                        {
                            "stream": "stdout",
                            "data": out_chunk.decode("utf-8", "replace"),
                        }
                    )
                if err_chunk:
                    _put(
                        {
                            "stream": "stderr",
                            "data": err_chunk.decode("utf-8", "replace"),
                        }
                    )
            result = container.wait(timeout=timeout)
            exit_code = int(result.get("StatusCode", 1))
            _put(_exit_frame(exit_code, time.time() - start_time, rate))
        except Exception as e:
            logger.error(f"Sandbox (Docker) stream failed: {e}")
            _put({"stream": "stderr", "data": str(e)})
            _put(_exit_frame(1, time.time() - start_time, rate))
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception as e:
                    logger.warning(f"Failed to remove sandbox container: {e}")
            _put(None)

    reader = loop.run_in_executor(None, _reader)
    deadline = start_time + timeout
    timed_out = False
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            timed_out = True
            break
        try:
            frame = await asyncio.wait_for(queue.get(), timeout=remaining)
        except TimeoutError:
            timed_out = True
            break
        if frame is None:
            break
        yield frame

    if not timed_out:
        # The sentinel is the reader's last act, so this resolves promptly;
        # it surfaces any unexpected executor failure instead of hiding it.
        await reader

    if timed_out:
        container = state.get("container")
        if container is not None:
            try:
                await loop.run_in_executor(None, lambda: container.kill())
            except Exception as e:
                logger.warning(f"Failed to kill timed-out sandbox container: {e}")
        yield {
            "stream": "stderr",
            "data": f"Execution timed out after {timeout}s; container killed",
        }
        yield _exit_frame(-1, float(timeout), rate)


async def stream_sbx_execution(
    service: SandboxService,
    code: str,
    language: str,
    timeout: int,
    mounts: dict[str, str] | None,
    envs: dict[str, str] | None,
    rate: float,
) -> AsyncIterator[StreamFrame]:
    """Degraded streaming fallback for the sbx provider.

    The sbx CLI client has no streaming primitive, so the execution runs to
    completion and the collected stdout/stderr are emitted as single frames
    before the exit frame.

    Args:
        service: The owning sandbox service (provides the sbx factory).
        code: Code to execute.
        language: Language runtime.
        timeout: Execution timeout in seconds.
        mounts: host_path:container_path volume mappings.
        envs: Environment variables for the sandbox.
        rate: USD per compute second (``cost_per_compute_second``).

    Yields:
        At most one stdout and one stderr frame, then the exit frame.
    """
    logger.info(
        "sbx provider has no streaming primitive; "
        "degrading to run-to-completion single frames"
    )
    result = await service._execute_sbx_async(code, language, timeout, mounts, envs)
    if result.stdout:
        yield {"stream": "stdout", "data": result.stdout}
    if result.stderr:
        yield {"stream": "stderr", "data": result.stderr}
    yield _exit_frame(result.exit_code, float(result.execution_time), rate)
