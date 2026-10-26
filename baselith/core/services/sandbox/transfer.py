"""Run sandboxed code with files moved through the container's stdin and stdout.

The Docker provider's answer to "give the code these files and bring back what
it writes" without bind mounts, so it works the same against the local socket,
a remote TCP daemon and Docker-in-Docker. The container is created with the
usual hardened runtime kwargs (:func:`~.policy.build_sandbox_runtime_kwargs`,
per-call resources included) plus an open stdin. Two attach streams are opened
before it starts: one carries the framed tar into stdin, the other brings
stdout and stderr back, demultiplexed by a reader thread into bounded windows.
Nothing goes through the daemon's logs, so log rotation, retention and the
logging driver cannot lose an archive (file runs use the ``none`` driver).
See :mod:`~baselith.core.services.sandbox.bootstrap` for the protocol and the
trust model.
"""

from __future__ import annotations

import asyncio
import secrets
import socket
import struct
import threading
import time
from collections import deque
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from baselith.core.observability.logging import get_logger

from .bootstrap import (
    ARCHIVE_MISSING,
    BOOTSTRAP,
    COLLECTION_KEY,
    ENV_BOUNDARY,
    ENV_DEADLINE,
    ENV_MAX_INPUT_BYTES,
    ENV_MAX_OUTPUT_BYTES,
    ENV_MAX_OUTPUT_FILES,
    ENV_PREFIX,
    USER_STDOUT_CAP,
    FileTransfer,
    bootstrap_deadline,
    decode_stdout,
    encoded_output_cap,
    pack_inputs,
    unpack_outputs,
)
from .policy import FSIZE_LIMIT_BYTES, build_sandbox_runtime_kwargs
from .resources import Resources, clamp

if TYPE_CHECKING:
    from baselith.core.config.sandbox import SandboxConfig

    from .docker_factory import DockerFactory
    from .service import ExecutionResult

logger = get_logger(__name__)

#: Bytes of stderr kept from each end of the stream.
STDERR_KEEP = 256 * 1024
#: Seconds the reader may take to drain the stream after the container exits.
READ_GRACE_S = 10.0
#: Appended to stderr when no usable archive arrived.
ARCHIVE_MISSING_NOTE = (
    "sandbox: the run's output archive never arrived (the container was killed, "
    "ran out of memory, or the bootstrap failed); no files were returned"
)


class _Window:
    """Keep the first ``head`` and about the last ``tail`` bytes of a stream.

    Whole chunks fall off the front of the tail, so memory stays near
    ``head + tail + one chunk`` however much the container printed.
    """

    def __init__(self, head: int, tail: int) -> None:
        self._head_cap, self._tail_cap = head, tail
        self._head = bytearray()
        self._tail: deque[bytes] = deque()
        self._tail_size = 0
        self.dropped = 0

    def feed(self, chunk: bytes) -> None:
        room = self._head_cap - len(self._head)
        if room > 0:
            self._head += chunk[:room]
            chunk = chunk[room:]
        if not chunk:
            return
        self._tail.append(chunk)
        self._tail_size += len(chunk)
        while self._tail and self._tail_size - len(self._tail[0]) >= self._tail_cap:
            gone = self._tail.popleft()
            self._tail_size -= len(gone)
            self.dropped += len(gone)

    def value(self) -> bytes:
        chunks, dropped = list(self._tail), self.dropped
        extra = self._tail_size - self._tail_cap
        if extra > 0 and chunks:  # feed() keeps extra below the first chunk
            chunks[0], dropped = chunks[0][extra:], dropped + extra
        note = [b"\n[... %d bytes dropped ...]\n" % dropped] if dropped else []
        return b"".join([bytes(self._head), *note, *chunks])  # one copy


class _Demux:
    """Split Docker's multiplexed attach stream into stdout and stderr windows.

    Each frame is an 8-byte header (stream id, three zero bytes, big-endian
    length) followed by the payload; payloads are forwarded as they arrive, so
    no frame is ever buffered whole.
    """

    def __init__(self, stdout: _Window, stderr: _Window) -> None:
        self._windows = {1: stdout, 2: stderr}
        self._header = bytearray()
        self._kind = 1
        self._left = 0

    def feed(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            if self._left == 0:
                need = 8 - len(self._header)
                self._header += view[:need]
                view = view[need:]
                if len(self._header) == 8:
                    self._kind, self._left = struct.unpack(">BxxxL", self._header)
                    self._header.clear()
                continue
            part = view[: self._left]
            view = view[len(part) :]
            self._left -= len(part)
            self._windows.get(self._kind, self._windows[1]).feed(bytes(part))


def _raw(attached: Any) -> Any:
    """The object with ``sendall``/``recv``: ``SocketIO._sock`` or the socket."""
    return getattr(attached, "_sock", None) or attached


def _pump(attached: Any, demux: _Demux, timeout: float) -> None:
    """Reader thread body: forward the attach stream until EOF or an error."""
    raw = _raw(attached)
    try:
        if hasattr(raw, "settimeout"):
            raw.settimeout(timeout)
        while chunk := raw.recv(65536):
            demux.feed(chunk)
    except Exception as exc:  # closed under us, timed out, daemon gone
        logger.debug("sandbox_file_transfer_reader_stopped", error=str(exc))


def _send(attached: Any, payload: bytes, timeout: float) -> None:
    """Write the framed tar to the stdin attach stream, then half-close it.

    docker-py hands back a ``SocketIO`` wrapping the real socket for unix
    sockets, the TLS socket itself for ``https``, or a paramiko channel for
    ssh; all of them expose ``sendall`` on ``_sock`` or on themselves. The
    stream is used by this thread only, so a TLS socket is never shared.
    """
    raw = _raw(attached)
    if hasattr(raw, "settimeout"):
        raw.settimeout(timeout)
    raw.sendall(payload)
    try:
        raw.shutdown(socket.SHUT_WR)
    except Exception:  # silent-ok: the bootstrap reads an exact length, EOF is hygiene
        pass


def _close(attached: Any) -> None:
    """Close an attach stream and the HTTP response docker-py pinned to it."""
    raw = _raw(attached)
    for step in (
        lambda: raw.shutdown(socket.SHUT_RDWR),
        attached.close,
        lambda: attached._response.close(),
    ):
        try:
            step()
        except Exception:  # silent-ok: best-effort teardown of a finished stream
            pass


def _environment(
    envs: Mapping[str, str] | None, files: FileTransfer, boundary: str, timeout: float
) -> dict[str, str]:
    """Caller env minus anything that could steer the bootstrap, plus its own."""
    environment = {
        key: value
        for key, value in (envs or {}).items()
        if not key.startswith(ENV_PREFIX)
    }
    environment.update(
        {
            ENV_BOUNDARY: boundary,
            ENV_MAX_INPUT_BYTES: str(sum(len(data) for data in files.inputs.values())),
            ENV_MAX_OUTPUT_FILES: str(files.max_output_files),
            ENV_MAX_OUTPUT_BYTES: str(files.max_output_bytes),
            ENV_DEADLINE: f"{bootstrap_deadline(timeout):.3f}",
        }
    )
    return environment


def validate_transfer(
    files: FileTransfer,
    provider: str,
    mounts: Mapping[str, str] | None,
    language: str,
    resources: Resources | None,
    config: SandboxConfig,
) -> None:
    """Refuse a file run the docker bootstrap cannot honour.

    Raises:
        ValueError: The sbx provider, ``mounts`` alongside ``files``, a language
            other than python, an input over the per-file ``fsize`` limit
            (64 MiB), or inputs larger than the run's ``/tmp``.
    """
    if provider == "sbx":
        raise ValueError("file transfer needs the docker sandbox provider")
    if mounts:
        raise ValueError("file transfer cannot be combined with mounts")
    if language.lower() != "python":
        raise ValueError("file transfer runs python code only")
    for name, data in files.inputs.items():
        if len(data) > FSIZE_LIMIT_BYTES:
            raise ValueError(
                f"input {name!r} is larger than the sandbox's per-file limit "
                f"({FSIZE_LIMIT_BYTES // (1024 * 1024)} MiB)"
            )
    tmpfs_bytes = clamp(resources, config)[1] * 1024 * 1024
    if sum(len(data) for data in files.inputs.values()) > tmpfs_bytes:
        raise ValueError("file transfer inputs are larger than the sandbox's /tmp")


async def execute_with_files(
    factory: DockerFactory,
    code: str,
    files: FileTransfer,
    envs: Mapping[str, str] | None,
    resources: Resources | None,
    timeout: int,
) -> ExecutionResult:
    """Run ``code`` with ``files`` in a docker sandbox, off the event loop.

    The runtime kwargs are built here, on the loop's thread: they read the
    (possibly overridden) sandbox config, which is context-scoped, and
    contextvars do not follow the call into ``run_in_executor``.
    """
    await factory.ensure_image()
    runtime_kwargs = build_sandbox_runtime_kwargs(resources=resources)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, run_with_files, factory, code, files, envs, runtime_kwargs, timeout
    )


def run_with_files(
    factory: DockerFactory,
    code: str,
    files: FileTransfer,
    envs: Mapping[str, str] | None,
    runtime_kwargs: Mapping[str, Any],
    timeout: int,
) -> ExecutionResult:
    """Blocking file run: create, attach, start, feed, wait, drain, remove.

    Both attach streams are opened *before* ``start`` so no byte is missed;
    stdin is fed *after* it, while the reader thread already drains stdout and
    stderr, so neither side can stall on a full buffer. The container is
    always removed. Exit 0 without a usable archive is reported as a failure
    (exit 1, ``output_refusals == {"*": "archive_missing"}`` and a stderr
    note); on the parent's own timeout the container is killed and the
    result has exit code 124, the output captured so far and no files.

    Args:
        factory: The docker factory (client and image).
        code: Python source, delivered as a tar member rather than argv.
        files: Inputs and output limits.
        envs: Extra environment; ``BASELITH_SANDBOX_*`` keys are dropped.
        runtime_kwargs: Hardened kwargs from ``build_sandbox_runtime_kwargs``.
        timeout: Wall-clock seconds for the whole run.

    Returns:
        The run's result with ``outputs`` and ``output_refusals`` filled.
    """
    from .service import ExecutionResult

    start = time.monotonic()
    boundary = secrets.token_hex(16)
    container: Any = None
    streams: list[Any] = []
    tail = encoded_output_cap(files.max_output_files, files.max_output_bytes) + 64
    out = _Window(USER_STDOUT_CAP, tail)
    err = _Window(STDERR_KEEP, STDERR_KEEP)
    try:
        payload = pack_inputs(files.inputs, code)
        kwargs = dict(runtime_kwargs)
        kwargs.setdefault("log_config", {"type": "none", "config": {}})
        # docker-py has no ``stdin_once`` argument: a create that is not
        # detached and has ``stdin_open`` sets OpenStdin and StdinOnce, so the
        # daemon closes the code's stdin once this client lets go.
        container = factory.client.containers.create(
            factory.base_image,
            command=["python", "-c", BOOTSTRAP],
            stdin_open=True,
            environment=_environment(envs, files, boundary, timeout),
            **kwargs,
        )
        output = container.attach_socket(params={"stdout": 1, "stderr": 1, "stream": 1})
        streams.append(output)
        stdin = container.attach_socket(params={"stdin": 1, "stream": 1})
        streams.append(stdin)
        reader = threading.Thread(
            target=_pump,
            args=(output, _Demux(out, err), timeout + READ_GRACE_S),
            name="sandbox-attach-reader",
            daemon=True,
        )
        reader.start()
        container.start()
        try:
            _send(stdin, payload, timeout)
        except OSError as exc:  # the bootstrap exited early; its stderr says why
            logger.warning("sandbox_file_transfer_stdin_failed", error=str(exc))
        try:
            remaining = max(timeout - (time.monotonic() - start), 0.1)
            exit_code = container.wait(timeout=remaining).get("StatusCode", 1)
        except Exception:
            try:
                container.kill()
            except Exception as exc:
                logger.warning("sandbox_file_transfer_kill_failed", error=str(exc))
            reader.join(READ_GRACE_S)
            stderr = err.value().decode("utf-8", errors="replace").strip()
            return ExecutionResult(
                stdout=decode_stdout(out.value()),
                stderr=f"{stderr}\nExecution timed out".strip(),
                exit_code=124,
                execution_time=timeout,
            )
        reader.join(READ_GRACE_S)
        stdout, outputs, refusals = unpack_outputs(
            out.value(),
            boundary,
            max_files=files.max_output_files,
            max_bytes=files.max_output_bytes,
        )
        stderr = err.value().decode("utf-8", errors="replace").strip()
        if refusals.get(COLLECTION_KEY) == ARCHIVE_MISSING:
            stderr = f"{stderr}\n{ARCHIVE_MISSING_NOTE}".strip()
            exit_code = exit_code or 1
        return ExecutionResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            execution_time=time.monotonic() - start,
            outputs=outputs,
            output_refusals=refusals,
        )
    except Exception as exc:
        logger.error("sandbox_file_transfer_failed", error=str(exc))
        return ExecutionResult(
            stdout="",
            stderr=str(exc),
            exit_code=1,
            execution_time=time.monotonic() - start,
        )
    finally:
        for stream in streams:
            _close(stream)
        if container is not None:
            try:
                container.remove(force=True)
            except Exception as exc:
                logger.warning("sandbox_file_transfer_remove_failed", error=str(exc))
