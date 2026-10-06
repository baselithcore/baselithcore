"""Streaming execution tests for SandboxService.execute_code_stream.

Docker and sbx backends are fully mocked — no real containers are started.
"""

import asyncio
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.config.sandbox import SandboxConfig
from core.orchestration.limits import BudgetExceededError, LoopBudget, LoopLimits


@pytest.fixture
def mock_docker_pkg():
    """Patch the docker package where docker_factory imports it."""
    with patch("core.services.sandbox.docker_factory.docker") as mock_docker:

        class MockDockerException(Exception):
            pass

        mock_docker.errors.DockerException = MockDockerException
        yield mock_docker


@pytest.fixture
def mock_docker_client(mock_docker_pkg):
    client = MagicMock()
    mock_docker_pkg.from_env.return_value = client
    return client


def patched_config(**overrides):
    """Patch the config seam the service reads, with field overrides."""
    return patch(
        "core.services.sandbox.service.get_sandbox_config",
        return_value=SandboxConfig(**overrides),
    )


def make_container(
    mock_docker_client,
    attach_frames: list[tuple[bytes | None, bytes | None]],
    exit_code: int = 0,
    wait_sleep: float = 0.0,
):
    """Build a mock container with a demuxed attach stream."""
    container = MagicMock()
    container.attach.side_effect = lambda **kwargs: iter(attach_frames)

    def _wait(timeout=None):
        if wait_sleep:
            time.sleep(wait_sleep)
        return {"StatusCode": exit_code}

    container.wait.side_effect = _wait
    mock_docker_client.containers.run.return_value = container
    return container


async def collect(stream):
    return [frame async for frame in stream]


async def test_stream_yields_frames_in_order_with_exit(mock_docker_client):
    from core.services.sandbox.service import SandboxService

    container = make_container(
        mock_docker_client,
        [(b"line1\n", None), (None, b"warn\n"), (b"line2\n", None)],
    )
    with patched_config(cost_per_compute_second=0.5):
        service = SandboxService()
        frames = await collect(service.execute_code_stream('print("hi")'))

    assert [f["stream"] for f in frames] == ["stdout", "stderr", "stdout", "exit"]
    assert frames[0]["data"] == "line1\n"
    assert frames[1]["data"] == "warn\n"
    assert frames[2]["data"] == "line2\n"
    exit_frame = frames[-1]
    assert exit_frame["exit_code"] == 0
    assert exit_frame["compute_seconds"] >= 0.0
    assert exit_frame["cost_usd"] == pytest.approx(exit_frame["compute_seconds"] * 0.5)
    container.remove.assert_called_with(force=True)


async def test_stream_timeout_kills_and_reports(mock_docker_client):
    from core.services.sandbox.service import SandboxService

    release = threading.Event()

    def attach_frames(**kwargs):
        yield (b"partial", None)
        release.wait(10)

    container = MagicMock()
    container.attach.side_effect = lambda **kwargs: attach_frames()
    container.kill.side_effect = lambda *a, **k: release.set()
    container.wait.return_value = {"StatusCode": 137}
    mock_docker_client.containers.run.return_value = container

    with patched_config(cost_per_compute_second=2.0):
        service = SandboxService()
        frames = await collect(service.execute_code_stream("print('x')", timeout=1))

    assert frames[0] == {"stream": "stdout", "data": "partial"}
    assert frames[-2]["stream"] == "stderr"
    assert "timed out" in frames[-2]["data"].lower()
    exit_frame = frames[-1]
    assert exit_frame["stream"] == "exit"
    assert exit_frame["exit_code"] == -1
    assert exit_frame["compute_seconds"] == pytest.approx(1.0)
    assert exit_frame["cost_usd"] == pytest.approx(2.0)
    container.kill.assert_called()


async def test_stream_static_analysis_blocks_before_container(mock_docker_client):
    from core.services.sandbox.service import SandboxService

    with patched_config(static_analysis_mode="block", cost_per_compute_second=1.0):
        service = SandboxService()
        frames = await collect(service.execute_code_stream("import socket"))

    assert [f["stream"] for f in frames] == ["stderr", "exit"]
    assert "blocked imports" in frames[0]["data"]
    assert frames[1]["exit_code"] == 1
    assert frames[1]["compute_seconds"] == 0.0
    assert frames[1]["cost_usd"] == 0.0
    mock_docker_client.containers.run.assert_not_called()


async def test_stream_unsupported_language_docker(mock_docker_client):
    from core.services.sandbox.service import SandboxService

    with patched_config():
        service = SandboxService()
        frames = await collect(
            service.execute_code_stream("console.log('hi')", language="javascript")
        )

    assert [f["stream"] for f in frames] == ["stderr", "exit"]
    assert "Unsupported language" in frames[0]["data"]
    assert frames[1]["exit_code"] == 1
    mock_docker_client.containers.run.assert_not_called()


async def test_stream_sbx_degraded_fallback(mock_docker_pkg):
    from core.services.sandbox.service import SandboxService

    sbx_factory = MagicMock()
    sbx_factory.ensure_available = AsyncMock()
    sbx_factory.client.run = AsyncMock(return_value=("hello", "warn", 0))

    with patched_config(cost_per_compute_second=0.1):
        service = SandboxService(sbx_factory=sbx_factory, provider="sbx")
        frames = await collect(service.execute_code_stream('print("hello")'))

    assert [f["stream"] for f in frames] == ["stdout", "stderr", "exit"]
    assert frames[0]["data"] == "hello"
    assert frames[1]["data"] == "warn"
    assert frames[2]["exit_code"] == 0
    assert frames[2]["cost_usd"] == pytest.approx(frames[2]["compute_seconds"] * 0.1)


async def test_stream_budget_charged(mock_docker_client):
    from core.services.sandbox.service import SandboxService

    make_container(mock_docker_client, [(b"ok", None)], wait_sleep=0.01)
    budget = LoopBudget(limits=LoopLimits(budget_usd=100.0))
    with patched_config(cost_per_compute_second=1.0):
        service = SandboxService()
        frames = await collect(
            service.execute_code_stream('print("ok")', budget=budget)
        )

    exit_frame = frames[-1]
    assert exit_frame["cost_usd"] > 0.0
    assert budget.cost_usd == pytest.approx(exit_frame["cost_usd"])


async def test_stream_budget_exceeded_propagates(mock_docker_client):
    from core.services.sandbox.service import SandboxService

    make_container(mock_docker_client, [(b"ok", None)], wait_sleep=0.01)
    budget = LoopBudget(limits=LoopLimits(budget_usd=1e-9))
    seen = []
    with patched_config(cost_per_compute_second=1.0):
        service = SandboxService()
        with pytest.raises(BudgetExceededError):
            async for frame in service.execute_code_stream(
                'print("ok")', budget=budget
            ):
                seen.append(frame)

    assert any(f["stream"] == "stdout" for f in seen)
    assert all(f["stream"] != "exit" for f in seen)


async def test_stream_output_is_capped_with_a_truncation_marker(
    mock_docker_client, monkeypatch
):
    from core.services.sandbox import streaming
    from core.services.sandbox.service import SandboxService

    monkeypatch.setattr(streaming, "MAX_STREAM_OUTPUT_BYTES", 10)
    container = make_container(
        mock_docker_client,
        [(b"12345678", None), (b"abcdef", None), (b"more", None)],
    )
    with patched_config():
        service = SandboxService()
        frames = await collect(service.execute_code_stream("print(1)"))

    data = [f for f in frames if f["stream"] in ("stdout", "stderr")]
    assert data[0]["data"] == "12345678"
    assert data[1]["data"] == "ab"
    assert data[2].get("truncated") is True
    assert len(data) == 3  # everything after the cap is dropped
    assert frames[-1]["stream"] == "exit"
    container.remove.assert_called_with(force=True)


async def test_stream_queue_is_bounded_and_applies_backpressure(
    mock_docker_client, monkeypatch
):
    from core.services.sandbox import streaming
    from core.services.sandbox.service import SandboxService

    monkeypatch.setattr(streaming, "STREAM_QUEUE_MAXSIZE", 2)
    produced: list[int] = []

    def attach_frames(**kwargs):
        for i in range(50):
            produced.append(i)
            yield (f"{i}\n".encode(), None)

    container = MagicMock()
    container.attach.side_effect = lambda **kwargs: attach_frames()
    container.wait.return_value = {"StatusCode": 0}
    mock_docker_client.containers.run.return_value = container

    with patched_config():
        service = SandboxService()
        stream = service.execute_code_stream("print(1)")
        first = await stream.__anext__()
        assert first["data"] == "0\n"
        await asyncio.sleep(0.3)
        # The reader cannot run ahead of a slow consumer by more than the
        # queue plus the frame it is blocked on.
        assert len(produced) <= 5
        rest = [f async for f in stream]

    assert len(produced) == 50
    assert rest[-1]["stream"] == "exit"


async def test_disconnect_mid_stream_kills_the_container(mock_docker_client):
    from core.services.sandbox.service import SandboxService

    release = threading.Event()

    def attach_frames(**kwargs):
        yield (b"first", None)
        release.wait(10)
        yield (b"never", None)

    container = MagicMock()
    container.attach.side_effect = lambda **kwargs: attach_frames()
    container.kill.side_effect = lambda *a, **k: release.set()
    container.wait.return_value = {"StatusCode": 137}
    mock_docker_client.containers.run.return_value = container

    with patched_config():
        service = SandboxService()
        stream = service.execute_code_stream("print(1)", timeout=30)
        assert (await stream.__anext__())["data"] == "first"
        await stream.aclose()  # the client went away

    for _ in range(50):
        if container.remove.called:
            break
        await asyncio.sleep(0.05)
    container.kill.assert_called()
    container.remove.assert_called_with(force=True)


async def test_direct_aclose_kills_the_container(mock_docker_client):
    """The backend generator itself cleans up, whatever wraps it."""
    from core.services.sandbox.service import SandboxService
    from core.services.sandbox.streaming import stream_docker_execution

    release = threading.Event()

    def attach_frames(**kwargs):
        yield (b"first", None)
        release.wait(10)

    container = MagicMock()
    container.attach.side_effect = lambda **kwargs: attach_frames()
    container.kill.side_effect = lambda *a, **k: release.set()
    container.wait.return_value = {"StatusCode": 137}
    mock_docker_client.containers.run.return_value = container

    with patched_config():
        service = SandboxService()
        service.docker_factory.ensure_image = AsyncMock()
        gen = stream_docker_execution(
            service, "print(1)", "python", 30, None, None, 0.0
        )
        assert (await gen.__anext__())["data"] == "first"
        await gen.aclose()

    for _ in range(50):
        if container.remove.called:
            break
        await asyncio.sleep(0.05)
    container.kill.assert_called()
    container.remove.assert_called_with(force=True)
