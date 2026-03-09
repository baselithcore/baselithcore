import asyncio
from unittest.mock import MagicMock, AsyncMock
import pytest

from core.services.sandbox.pool import SandboxPool, PoolConfig, PooledContainer


@pytest.fixture
def mock_container():
    c = MagicMock()
    c.id = "container-123"
    c.exec_run.return_value = (0, (b"hello", b""))
    c.start = MagicMock()
    c.kill = MagicMock()
    c.remove = MagicMock()
    return c


@pytest.fixture
def mock_docker_factory(mock_container):
    factory = MagicMock()
    factory.ensure_image = AsyncMock()
    factory.client.containers.create.return_value = mock_container
    factory.base_image = "python:3.10-slim"
    return factory


@pytest.fixture
def pool(mock_docker_factory):
    config = PoolConfig(
        min_size=1, max_size=2, health_check_interval=0.05, container_timeout=0.05
    )
    pool = SandboxPool(config=config, docker_factory=mock_docker_factory)
    return pool


@pytest.mark.asyncio
async def test_pool_warmup(pool, mock_docker_factory):
    # Starting pool should warm it up to min_size
    await pool.start()

    assert pool._available.qsize() == 1
    mock_docker_factory.ensure_image.assert_awaited_once()
    assert pool._total_created == 1

    await pool.stop()


@pytest.mark.asyncio
async def test_acquire_release(pool):
    await pool.start()

    async with pool.acquire() as container:
        assert isinstance(container, PooledContainer)
        assert container.id == "container-123"
        assert len(pool._in_use) == 1
        assert pool._available.qsize() == 0

    # After exit, container should be back in pool
    assert len(pool._in_use) == 0
    assert pool._available.qsize() == 1

    await pool.stop()


@pytest.mark.asyncio
async def test_acquire_create_new(pool):
    # Set min_size=0 so pool starts empty
    pool._config.min_size = 0
    await pool.start()

    assert pool._available.qsize() == 0

    # Acquire should create new since we are under max_size
    async with pool.acquire() as c:
        assert c.id == "container-123"
        assert pool._total_created == 1

    await pool.stop()


@pytest.mark.asyncio
async def test_execute_code(pool, mock_container):
    await pool.start()

    code = "print('hello')"
    result = await pool.execute(code)

    # Mock container exec_run
    mock_container.exec_run.assert_called()
    cmd = mock_container.exec_run.call_args[1]["cmd"]
    assert cmd == ["python", "-c", "print('hello')"]

    assert result["exit_code"] == 0
    assert result["stdout"] == "hello"

    await pool.stop()


@pytest.mark.asyncio
async def test_recycling(pool, mock_container):
    pool._config.max_executions = 1
    await pool.start()

    # 1st execution
    async with pool.acquire() as _:
        pass
    # Should have been recycled because max_exec=1
    # Since it's destroyed, it's not in available immediately, but warm_pool is scheduled
    # Let's wait a bit for async warm_pool
    await asyncio.sleep(0.05)

    assert pool._total_recycled == 1
    assert pool._total_created >= 2  # 1 initial + 1 after recycle

    await pool.stop()


@pytest.mark.asyncio
async def test_health_check(pool):
    pool._config.min_size = 1
    pool._config.health_check_interval = 0.1
    await pool.start()

    # Manually mark container as unhealthy
    pooled = await pool._available.get()
    pooled.is_healthy = False
    await pool._available.put(pooled)

    # Wait for health check loop
    await asyncio.sleep(0.2)

    # Should have been recycled
    assert pool._total_recycled >= 1

    await pool.stop()
