"""A file run against a mocked docker client: the plumbing, not the bootstrap."""

from __future__ import annotations

import base64
import io
import random
import re
import socket
import struct
import tarfile
from collections import deque
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from baselith.core.services.sandbox import FileTransfer, Resources, SandboxService
from baselith.core.services.sandbox.bootstrap import (
    BOOTSTRAP,
    bootstrap_deadline,
    pack_inputs,
)
from baselith.core.services.sandbox.policy import build_sandbox_runtime_kwargs
from baselith.core.services.sandbox.transfer import _Demux, _Window, run_with_files


def _frame(kind: int, data: bytes) -> bytes:
    return struct.pack(">BxxxL", kind, len(data)) + data


class _Raw:
    """The socket under docker-py's ``SocketIO``: ``sendall`` / ``recv``."""

    def __init__(self, container: MagicMock) -> None:
        self.container = container
        self.chunks: deque[bytes] = deque()
        self.sent = bytearray()
        self.shut: list[int] = []
        self.timeout: float | None = None
        self.started_before_send = False
        self.fail_with: Exception | None = None

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def sendall(self, data: bytes) -> None:
        self.started_before_send = self.container.start.called
        if self.fail_with is not None:
            raise self.fail_with
        self.sent += data

    def recv(self, size: int) -> bytes:
        return self.chunks.popleft() if self.chunks else b""

    def shutdown(self, how: int) -> None:
        self.shut.append(how)


class _Attach:
    """The shape docker-py returns for a unix socket: a SocketIO with ``_sock``."""

    def __init__(self, raw: _Raw) -> None:
        self._sock = raw
        self._response = MagicMock()
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _archive_stdout(boundary: str, outputs: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in outputs.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return b"\n" + boundary.encode() + b"\n" + base64.b64encode(buf.getvalue()) + b"\n"


def _factory(
    *,
    outputs: dict[str, bytes] | None = None,
    user: list[bytes] | None = None,
    stderr: bytes = b"",
    exit_code: int = 0,
    send_archive: bool = True,
) -> tuple[MagicMock, MagicMock, _Attach, _Attach]:
    factory = MagicMock()
    factory.ensure_image = AsyncMock()
    factory.base_image = "agent-sandbox:latest"
    container = MagicMock()
    output, stdin = _Attach(_Raw(container)), _Attach(_Raw(container))
    container.attach_socket.side_effect = lambda **kw: (
        output if kw["params"].get("stdout") else stdin
    )
    container.wait.return_value = {"StatusCode": exit_code}

    def create(image: str, **kwargs: Any) -> MagicMock:
        boundary = kwargs["environment"]["BASELITH_SANDBOX_BOUNDARY"]
        stream = b"".join(_frame(1, chunk) for chunk in (user or [b"user says hi\n"]))
        stream += _frame(2, stderr)
        if send_archive:
            stream += _frame(1, _archive_stdout(boundary, outputs or {}))
        # Arbitrary recv sizes: frame headers and payloads split anywhere.
        rng, at = random.Random(7), 0
        while at < len(stream):
            step = rng.randint(1, 70_000)
            output._sock.chunks.append(stream[at : at + step])
            at += step
        return container

    factory.client.containers.create.side_effect = create
    return factory, container, output, stdin


def _run(factory: MagicMock, files: FileTransfer, **overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "code": "print('hi')\n",
        "files": files,
        "envs": {"FOO": "1"},
        "runtime_kwargs": build_sandbox_runtime_kwargs(),
        "timeout": 30,
    }
    kwargs.update(overrides)
    return run_with_files(factory, **kwargs)


def test_run_feeds_stdin_and_reads_both_streams_from_the_attach_socket() -> None:
    factory, container, output, stdin = _factory(
        outputs={"result.csv": b"a,b\n"}, stderr=b"warn\n"
    )
    files = FileTransfer(inputs={"data.csv": b"1,2\n"})
    result = _run(factory, files)

    assert result.exit_code == 0
    assert result.stdout == "user says hi"
    assert result.stderr == "warn"
    assert result.outputs == {"result.csv": b"a,b\n"}
    assert result.output_refusals == {}
    container.logs.assert_not_called()

    assert bytes(stdin._sock.sent) == pack_inputs(files.inputs, "print('hi')\n")
    assert stdin._sock.shut[0] == socket.SHUT_WR
    assert stdin._sock.started_before_send is True
    for stream in (output, stdin):
        assert stream.closed is True
        stream._response.close.assert_called_once()
    container.remove.assert_called_once_with(force=True)

    names = [call[0] for call in container.mock_calls]
    assert names.count("attach_socket") == 2
    assert max(i for i, n in enumerate(names) if n == "attach_socket") < names.index(
        "start"
    )
    assert names.index("start") < names.index("wait")
    params = [call.kwargs["params"] for call in container.attach_socket.call_args_list]
    assert params == [
        {"stdout": 1, "stderr": 1, "stream": 1},
        {"stdin": 1, "stream": 1},
    ]
    assert 0 < container.wait.call_args.kwargs["timeout"] <= 30


def test_the_container_is_created_hardened_with_stdin_open_and_no_logs() -> None:
    factory, _, _, _ = _factory()
    runtime = build_sandbox_runtime_kwargs(
        resources=Resources(memory_mb=100, tmpfs_mb=32)
    )
    _run(factory, FileTransfer(inputs={"a.txt": b"abc"}), runtime_kwargs=runtime)

    call = factory.client.containers.create.call_args
    assert call.args == ("agent-sandbox:latest",)
    kwargs = call.kwargs
    assert kwargs["command"] == ["python", "-c", BOOTSTRAP]
    assert kwargs["stdin_open"] is True
    # docker-py has no ``stdin_once`` argument: a non-detached create with
    # ``stdin_open`` is what sets StdinOnce on the container config.
    assert "stdin_once" not in kwargs and "detach" not in kwargs
    for key, value in runtime.items():
        assert kwargs[key] == value
    assert kwargs["log_config"] == {"type": "none", "config": {}}
    env = kwargs["environment"]
    assert env["FOO"] == "1"
    assert re.fullmatch(r"[0-9a-f]{32}", env["BASELITH_SANDBOX_BOUNDARY"])
    assert env["BASELITH_SANDBOX_MAX_INPUT_BYTES"] == "3"
    assert env["BASELITH_SANDBOX_MAX_OUTPUT_FILES"] == "16"
    assert env["BASELITH_SANDBOX_MAX_OUTPUT_BYTES"] == str(64 * 1024 * 1024)
    assert (
        env["BASELITH_SANDBOX_DEADLINE"] == f"{bootstrap_deadline(30):.3f}" == "27.000"
    )


def test_an_explicit_log_config_is_left_alone() -> None:
    factory, _, _, _ = _factory()
    runtime = {**build_sandbox_runtime_kwargs(), "log_config": {"type": "json-file"}}
    _run(factory, FileTransfer(inputs={}), runtime_kwargs=runtime)
    assert factory.client.containers.create.call_args.kwargs["log_config"] == {
        "type": "json-file"
    }


def test_the_boundary_is_random_per_run() -> None:
    seen = set()
    for _ in range(3):
        factory, _, _, _ = _factory()
        _run(factory, FileTransfer(inputs={}))
        env = factory.client.containers.create.call_args.kwargs["environment"]
        seen.add(env["BASELITH_SANDBOX_BOUNDARY"])
    assert len(seen) == 3


def test_the_caller_cannot_steer_the_bootstrap_through_envs() -> None:
    factory, _, _, _ = _factory()
    envs = {
        "BASELITH_SANDBOX_ROOT": "/",
        "BASELITH_SANDBOX_BOUNDARY": "a" * 32,
        "BASELITH_SANDBOX_MAX_OUTPUT_FILES": "100000",
        "BASELITH_SANDBOX_DEADLINE": "100000",
        "KEEP": "yes",
    }
    _run(factory, FileTransfer(inputs={}), envs=envs)
    env = factory.client.containers.create.call_args.kwargs["environment"]
    assert "BASELITH_SANDBOX_ROOT" not in env
    assert env["BASELITH_SANDBOX_BOUNDARY"] != "a" * 32
    assert env["BASELITH_SANDBOX_MAX_OUTPUT_FILES"] == "16"
    assert env["BASELITH_SANDBOX_DEADLINE"] == "27.000"
    assert env["KEEP"] == "yes"


def test_exit_zero_without_an_archive_is_a_failure() -> None:
    factory, container, _, _ = _factory(send_archive=False)
    result = _run(factory, FileTransfer(inputs={}))

    assert result.exit_code == 1
    assert result.outputs == {}
    assert result.output_refusals == {"*": "archive_missing"}
    assert "output archive never arrived" in result.stderr
    assert result.stdout == "user says hi"


def test_a_failing_exit_without_an_archive_keeps_its_code() -> None:
    factory, _, _, stdin = _factory(
        stderr=b"sandbox bootstrap: refused input x: bad_name\n",
        exit_code=2,
        send_archive=False,
    )
    stdin._sock.fail_with = BrokenPipeError("container went away")
    result = _run(factory, FileTransfer(inputs={"a.txt": b"A"}))

    assert result.exit_code == 2
    assert "bad_name" in result.stderr
    assert "output archive never arrived" in result.stderr
    assert result.output_refusals == {"*": "archive_missing"}


def test_a_timeout_kills_removes_and_keeps_what_was_printed() -> None:
    factory, container, output, stdin = _factory(
        send_archive=False, stderr=b"working\n"
    )
    container.wait.side_effect = Exception("read timed out")
    result = _run(factory, FileTransfer(inputs={}), timeout=7)

    assert result.exit_code == 124
    assert result.stderr == "working\nExecution timed out"
    assert result.stdout == "user says hi"
    assert result.execution_time == 7
    assert result.outputs == {}
    container.kill.assert_called_once()
    container.remove.assert_called_once_with(force=True)
    assert output.closed and stdin.closed


def test_a_failure_after_create_still_removes_the_container() -> None:
    factory, container, output, _ = _factory()
    container.start.side_effect = RuntimeError("boom")
    result = _run(factory, FileTransfer(inputs={}))

    assert result.exit_code == 1
    assert "boom" in result.stderr
    container.remove.assert_called_once_with(force=True)
    assert output.closed


def test_a_failed_create_reports_the_error() -> None:
    factory, container, _, _ = _factory()
    factory.client.containers.create.side_effect = RuntimeError("no daemon")
    result = _run(factory, FileTransfer(inputs={}))

    assert result.exit_code == 1
    assert "no daemon" in result.stderr
    container.remove.assert_not_called()


def test_huge_user_stdout_is_bounded_and_outputs_still_arrive() -> None:
    chatter = [b"x" * 65536 for _ in range(80)]  # 5 MiB
    factory, _, _, _ = _factory(outputs={"r.txt": b"done"}, user=chatter)
    files = FileTransfer(inputs={}, max_output_files=2, max_output_bytes=1024)
    result = _run(factory, files)

    assert len(result.stdout) < 1024 * 1024 + 100
    assert result.stdout.endswith("[... stdout truncated ...]")
    assert result.outputs == {"r.txt": b"done"}


def test_the_demux_survives_any_split_and_routes_by_stream() -> None:
    frames = [
        (1, b"out-1 "),
        (2, b"err-1 "),
        (1, b""),
        (0, b"stdin echo "),
        (1, b"out-2"),
    ]
    stream = b"".join(_frame(kind, data) for kind, data in frames)
    for size in (1, 3, 7, 8, 9, len(stream)):
        out, err = _Window(1 << 20, 1 << 20), _Window(1 << 20, 1 << 20)
        demux = _Demux(out, err)
        for at in range(0, len(stream), size):
            demux.feed(stream[at : at + size])
        assert out.value() == b"out-1 stdin echo out-2"
        assert err.value() == b"err-1 "


# --- SandboxService.execute_code_async(files=...) ---------------------------------


async def test_files_with_the_sbx_provider_raise() -> None:
    service = SandboxService(
        docker_factory=MagicMock(), sbx_factory=MagicMock(), provider="sbx"
    )
    with pytest.raises(
        ValueError, match="file transfer needs the docker sandbox provider"
    ):
        await service.execute_code_async("print(1)", files=FileTransfer(inputs={}))


@pytest.mark.parametrize(
    ("kwargs", "inputs", "message"),
    [
        ({"mounts": {"/host": "/work"}}, {}, "mounts"),
        ({"language": "sh"}, {}, "python"),
        ({}, {"big.bin": b"x" * (64 * 1024 * 1024 + 1)}, "per-file limit"),
        (
            {"resources": Resources(tmpfs_mb=16)},
            {"a.bin": b"x" * (16 * 1024 * 1024 + 1)},
            "/tmp",
        ),
    ],
)
async def test_files_refuse_what_the_bootstrap_cannot_honour(
    kwargs: dict[str, Any], inputs: dict[str, bytes], message: str
) -> None:
    factory, _, _, _ = _factory()
    service = SandboxService(docker_factory=factory, provider="docker")
    with pytest.raises(ValueError, match=re.escape(message)):
        await service.execute_code_async(
            "print(1)", files=FileTransfer(inputs=inputs), **kwargs
        )
    factory.client.containers.create.assert_not_called()


async def test_execute_code_async_dispatches_a_file_run() -> None:
    factory, _, _, _ = _factory(outputs={"out.txt": b"42"})
    service = SandboxService(docker_factory=factory, provider="docker")
    resources = Resources(memory_mb=100)
    result = await service.execute_code_async(
        "print(42)", files=FileTransfer(inputs={"in.txt": b"1"}), resources=resources
    )

    assert result.outputs == {"out.txt": b"42"}
    assert result.stdout == "user says hi"
    assert result.compute_seconds == result.execution_time
    factory.ensure_image.assert_awaited_once()
    factory.client.containers.run.assert_not_called()
    kwargs = factory.client.containers.create.call_args.kwargs
    for key, value in build_sandbox_runtime_kwargs(resources=resources).items():
        assert kwargs[key] == value


async def test_a_file_run_still_goes_through_static_analysis() -> None:
    factory, _, _, _ = _factory()
    service = SandboxService(docker_factory=factory, provider="docker")
    result = await service.execute_code_async(
        "def broken(:\n", files=FileTransfer(inputs={})
    )
    assert result.exit_code == 1
    assert "syntax error" in result.stderr
    factory.client.containers.create.assert_not_called()


async def test_plain_runs_keep_empty_outputs() -> None:
    factory, container, _, _ = _factory()
    container.logs.return_value = b"plain"
    factory.client.containers.run.return_value = container
    result = await SandboxService(
        docker_factory=factory, provider="docker"
    ).execute_code_async("print('plain')")
    assert result.stdout == "plain"
    assert result.outputs == {} and result.output_refusals == {}
