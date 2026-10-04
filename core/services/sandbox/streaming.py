"""
Streaming sandbox execution backends.

Implements the incremental-output side of
:meth:`~core.services.sandbox.service.SandboxService.execute_code_stream`.
Frames are plain dicts: ``{"stream": "stdout"|"stderr", "data": str}`` for
output, terminated by ``{"stream": "exit", "exit_code": int,
"compute_seconds": float, "cost_usd": float}``.

The Docker backend attaches to the container's demuxed output stream from a
worker thread (mirroring the service's to-thread pattern for blocking
docker-py calls) and forwards chunks through a *bounded* ``asyncio.Queue``.
The output is untrusted, so two limits apply: the queue holds at most
:data:`STREAM_QUEUE_MAXSIZE` frames and the reader blocks (backpressure on the
container's pipe) while the client is slow, and at most
:data:`MAX_STREAM_OUTPUT_BYTES` of output are forwarded — the rest is dropped
behind one truncation marker. When the consumer goes away (client disconnect,
cancellation, ``aclose``) the container is killed and the reader released,
so an abandoned stream cannot keep a container or a worker thread alive.
The sbx CLI has no streaming primitive, so its backend degrades to
run-to-completion and emits the collected output as single frames.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncGenerator
from concurrent.futures import CancelledError as FutureCancelledError
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger

from .policy import build_sandbox_runtime_kwargs

if TYPE_CHECKING:
    from .service import SandboxService

logger = get_logger(__name__)

StreamFrame = dict[str, Any]

#: Frames buffered between the reader thread and the consumer. A full queue
#: blocks the reader, which stops draining the container's output pipe.
STREAM_QUEUE_MAXSIZE = 256
#: Output bytes (stdout + stderr, decoded) forwarded per execution; anything
#: past it is dropped behind a single truncation marker.
MAX_STREAM_OUTPUT_BYTES = 8 * 1024 * 1024
#: How often a blocked reader re-checks whether the consumer has gone away.
_PUT_POLL_S = 0.25


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
) -> AsyncGenerator[StreamFrame, None]:
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
    queue: asyncio.Queue[StreamFrame | None] = asyncio.Queue(
        maxsize=STREAM_QUEUE_MAXSIZE
    )
    docker_mounts = _build_docker_mounts(mounts)
    state: dict[str, Any] = {"container": None}
    stop = threading.Event()
    budget = {"remaining": MAX_STREAM_OUTPUT_BYTES, "truncated": False}
    start_time = time.time()

    def _put(frame: StreamFrame | None) -> bool:
        """Hand a frame to the consumer, blocking while the queue is full.

        Returns:
            False once the consumer has gone away; the frame is dropped.
        """
        if stop.is_set():
            return False
        try:
            future = asyncio.run_coroutine_threadsafe(queue.put(frame), loop)
        except RuntimeError:  # loop closed under us
            return False
        # One put per frame, waited on in slices: re-submitting after a
        # timeout could enqueue the same frame twice.
        while True:
            try:
                future.result(timeout=_PUT_POLL_S)
                return True
            except FutureTimeoutError:
                if stop.is_set():
                    future.cancel()
                    return False
            except (FutureCancelledError, RuntimeError):  # loop went away
                return False

    def _put_output(stream_name: str, chunk: bytes) -> None:
        if budget["truncated"]:
            return  # keep draining the pipe, forward nothing
        text = chunk.decode("utf-8", "replace")
        size = len(text.encode("utf-8"))
        if size > budget["remaining"]:
            head = text.encode("utf-8")[: budget["remaining"]].decode("utf-8", "ignore")
            budget["truncated"] = True
            budget["remaining"] = 0
            if head:
                _put({"stream": stream_name, "data": head})
            _put(
                {
                    "stream": "stderr",
                    "data": (
                        f"[output truncated: more than {MAX_STREAM_OUTPUT_BYTES} bytes]"
                    ),
                    "truncated": True,
                }
            )
            return
        budget["remaining"] -= size
        _put({"stream": stream_name, "data": text})

    def _kill(container: Any) -> None:
        try:
            container.kill()
        except Exception as e:
            logger.warning(f"Failed to kill sandbox container: {e}")

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
            if stop.is_set():  # consumer left while the container started
                _kill(container)
                return
            stream = container.attach(
                stdout=True, stderr=True, stream=True, logs=True, demux=True
            )
            for out_chunk, err_chunk in stream:
                if stop.is_set():
                    break
                if out_chunk:
                    _put_output("stdout", out_chunk)
                if err_chunk:
                    _put_output("stderr", err_chunk)
            if stop.is_set():
                return
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
    finished = False
    try:
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                break  # timed out
            try:
                frame = await asyncio.wait_for(queue.get(), timeout=remaining)
            except TimeoutError:
                break
            if frame is None:
                finished = True
                break
            yield frame

        if finished:
            # The sentinel is the reader's last act, so this resolves promptly;
            # it surfaces any unexpected executor failure instead of hiding it.
            await reader
        else:
            stop.set()
            container = state.get("container")
            if container is not None:
                await loop.run_in_executor(None, _kill, container)
            yield {
                "stream": "stderr",
                "data": f"Execution timed out after {timeout}s; container killed",
            }
            yield _exit_frame(-1, float(timeout), rate)
            finished = True
    finally:
        if not finished:
            # The consumer left mid-stream (disconnect, cancellation, aclose):
            # release a reader blocked on the full queue and kill the
            # container so it does not run on unobserved. The reader's own
            # finally then removes it.
            stop.set()
            container = state.get("container")
            if container is not None:
                _kill_in_background(loop, _kill, container)


def _kill_in_background(
    loop: asyncio.AbstractEventLoop, kill: Any, container: Any
) -> None:
    """Kill without awaiting: a cancelled generator must not block on Docker."""
    try:
        loop.run_in_executor(None, kill, container)
    except RuntimeError:  # loop shutting down: kill inline
        kill(container)


async def stream_sbx_execution(
    service: SandboxService,
    code: str,
    language: str,
    timeout: int,
    mounts: dict[str, str] | None,
    envs: dict[str, str] | None,
    rate: float,
) -> AsyncGenerator[StreamFrame, None]:
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
