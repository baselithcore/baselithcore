"""
Sandbox Container Pool.

Provides pre-warmed container pooling for faster code execution.
Instead of creating and destroying containers for each execution,
this pool maintains a set of ready containers.

Usage:
    from core.services.sandbox.pool import SandboxPool

    pool = SandboxPool(min_size=2, max_size=10)
    await pool.start()

    # Get container from pool
    async with pool.acquire() as container:
        result = await container.execute("print('hello')")

    await pool.stop()
"""

from __future__ import annotations

import asyncio
from core.observability.logging import get_logger
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from contextlib import asynccontextmanager

logger = get_logger(__name__)


@dataclass
class PooledContainer:
    """A container managed by the pool."""

    id: str
    container: Any  # Docker Container
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    executions: int = 0
    is_healthy: bool = True


@dataclass
class PoolConfig:
    """Configuration for sandbox pool."""

    min_size: int = 2
    max_size: int = 10
    max_idle_time: float = 300.0  # 5 minutes
    max_executions: int = 100  # Recycle after N executions
    health_check_interval: float = 60.0
    container_timeout: float = 30.0


class SandboxPool:
    """
    Container pool for reusing sandbox containers.

    Maintains a pool of pre-warmed Docker containers to reduce
    the overhead of container creation for each code execution.

    Features:
    - Pre-warming: Maintains minimum number of ready containers
    - Auto-scaling: Spawns new containers on demand up to max_size
    - Recycling: Recycles containers after max_executions
    - Health checks: Periodically validates container health
    - Graceful shutdown: Cleans up all containers on stop

    Example:
        ```python
        pool = SandboxPool(min_size=2, max_size=5)
        await pool.start()

        async with pool.acquire() as container:
            # Execute code in pooled container
            result = await pool.execute_in(container, "print('hello')")

        await pool.stop()
        ```
    """

    def __init__(
        self,
        config: Optional[PoolConfig] = None,
        docker_factory: Optional[Any] = None,
    ) -> None:
        """
        Initialize SandboxPool.

        Args:
            config: Pool configuration
            docker_factory: DockerFactory instance (lazy loaded if None)
        """
        self._config = config or PoolConfig()
        self._docker_factory = docker_factory

        self._available: Optional[asyncio.Queue[PooledContainer]] = None
        self._in_use: set[str] = set()
        self._all_containers: dict[str, PooledContainer] = {}

        self._running = False
        self._health_task: Optional[asyncio.Task] = None
        self._lock: Optional[asyncio.Lock] = None

        # Stats
        self._total_created = 0
        self._total_recycled = 0
        self._total_executions = 0

    def _ensure_initialized(self) -> None:
        """Ensure asyncio primitives are initialized."""
        # This should only be called from async methods (start, stop, acquire, etc.)
        if self._available is None:
            self._available = asyncio.Queue()
        if self._lock is None:
            self._lock = asyncio.Lock()

        logger.info(
            f"SandboxPool configured: min={self._config.min_size}, "
            f"max={self._config.max_size}"
        )

    def _get_docker_factory(self) -> Any:
        """Lazy load Docker factory."""
        if self._docker_factory is None:
            from .docker_factory import DockerFactory

            self._docker_factory = DockerFactory()
        return self._docker_factory

    async def _create_container(self) -> Optional[PooledContainer]:
        """Create a new sandboxed container."""
        loop = asyncio.get_running_loop()
        try:
            factory = self._get_docker_factory()
            await factory.ensure_image()

            # Create container (but don't start it yet)
            def _create_impl():
                """Internal container creation operation."""
                return factory.client.containers.create(
                    factory.base_image,
                    command=["sleep", "infinity"],  # Keep alive
                    detach=True,
                    network_mode="none",
                    mem_limit="128m",
                    cpu_period=100000,
                    cpu_quota=50000,
                )

            container = await loop.run_in_executor(None, _create_impl)

            await loop.run_in_executor(None, container.start)

            pooled = PooledContainer(
                id=container.id,
                container=container,
            )

            self._all_containers[container.id] = pooled
            self._total_created += 1

            logger.debug(f"Created pooled container: {container.id[:12]}")
            return pooled

        except Exception as e:
            logger.error(f"Failed to create container: {e}")
            return None

    async def _destroy_container(self, pooled: PooledContainer) -> None:
        """Destroy a container."""
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, pooled.container.kill)
            await loop.run_in_executor(
                None, lambda: pooled.container.remove(force=True)
            )
            logger.debug(f"Destroyed container: {pooled.id[:12]}")
        except Exception as e:
            logger.warning(f"Failed to destroy container {pooled.id[:12]}: {e}")
        finally:
            self._all_containers.pop(pooled.id, None)
            self._in_use.discard(pooled.id)

    async def _warm_pool(self) -> None:
        """Ensure minimum number of containers are available."""
        self._ensure_initialized()
        assert self._available is not None
        current_available = self._available.qsize()
        needed = self._config.min_size - current_available - len(self._in_use)

        for _ in range(max(0, needed)):
            container = await self._create_container()
            if container:
                await self._available.put(container)

    async def _health_check_loop(self) -> None:
        """Periodically check container health and recycle stale ones."""
        self._ensure_initialized()
        assert self._available is not None
        while self._running:
            try:
                await asyncio.sleep(self._config.health_check_interval)

                if not self._running:
                    break

                # Check containers in pool
                now = time.time()
                temp_containers: list[PooledContainer] = []

                while not self._available.empty():
                    try:
                        pooled = self._available.get_nowait()

                        # Check if should recycle
                        should_recycle = (
                            (now - pooled.last_used) > self._config.max_idle_time
                            or pooled.executions >= self._config.max_executions
                            or not pooled.is_healthy
                        )

                        if should_recycle:
                            await self._destroy_container(pooled)
                            self._total_recycled += 1
                        else:
                            temp_containers.append(pooled)

                    except asyncio.QueueEmpty:
                        break

                # Put healthy containers back
                for c in temp_containers:
                    await self._available.put(c)

                # Warm pool back to minimum
                await self._warm_pool()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health check error: {e}")

    async def start(self) -> None:
        """Start the pool and warm it up."""
        self._ensure_initialized()
        assert self._available is not None
        if self._running:
            return

        self._running = True

        # Warm pool
        await self._warm_pool()

        # Start health check task
        self._health_task = asyncio.create_task(self._health_check_loop())

        logger.info(f"SandboxPool started with {self._available.qsize()} containers")

    async def stop(self) -> None:
        """Stop the pool and clean up all containers."""
        self._ensure_initialized()
        assert self._lock is not None
        self._running = False

        # Cancel health check
        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass

        # Destroy all containers
        async with self._lock:
            for pooled in list(self._all_containers.values()):
                await self._destroy_container(pooled)

            self._all_containers.clear()
            self._in_use.clear()

        logger.info(
            f"SandboxPool stopped. Stats: created={self._total_created}, "
            f"recycled={self._total_recycled}, executions={self._total_executions}"
        )

    @asynccontextmanager
    async def acquire(self, timeout: Optional[float] = None):
        """
        Acquire a container from the pool.

        Args:
            timeout: Max time to wait for available container

        Yields:
            PooledContainer instance
        """
        self._ensure_initialized()
        assert self._available is not None
        assert self._lock is not None
        timeout = timeout or self._config.container_timeout
        pooled: Optional[PooledContainer] = None

        try:
            # Try to get from pool
            try:
                pooled = await asyncio.wait_for(self._available.get(), timeout=timeout)
            except asyncio.TimeoutError:
                # Pool empty, try to create new container if under max
                async with self._lock:
                    total = len(self._all_containers)
                    if total < self._config.max_size:
                        pooled = await self._create_container()

                if pooled is None:
                    raise RuntimeError("Pool exhausted and at max capacity") from None

            assert pooled is not None
            self._in_use.add(pooled.id)
            pooled.last_used = time.time()

            yield pooled

        finally:
            if pooled:
                self._in_use.discard(pooled.id)
                pooled.executions += 1
                self._total_executions += 1

                # Check if should recycle
                if pooled.executions >= self._config.max_executions:
                    await self._destroy_container(pooled)
                    self._total_recycled += 1
                    # Trigger pool warm
                    asyncio.create_task(self._warm_pool())
                else:
                    await self._available.put(pooled)

    async def execute(
        self,
        code: str,
        language: str = "python",
        timeout: float = 30.0,
    ) -> dict:
        """
        Execute code using a pooled container.

        Args:
            code: Code to execute
            language: Language (currently only python)
            timeout: Execution timeout

        Returns:
            Dict with stdout, stderr, exit_code, execution_time
        """
        async with self.acquire() as pooled:
            return await self._execute_in_container(pooled, code, language, timeout)

    async def _execute_in_container(
        self,
        pooled: PooledContainer,
        code: str,
        language: str,
        timeout: float,
    ) -> dict:
        """Execute code in a specific container."""
        if language.lower() != "python":
            return {
                "stdout": "",
                "stderr": f"Unsupported language: {language}",
                "exit_code": 1,
                "execution_time": 0,
            }

        start_time = time.time()

        try:
            # Execute code in container
            loop = asyncio.get_running_loop()

            def _exec():
                """Internal exec execution operation."""
                return pooled.container.exec_run(
                    cmd=["python", "-c", code],
                    demux=True,
                )

            exit_code, output = await loop.run_in_executor(None, _exec)

            stdout = output[0].decode("utf-8").strip() if output[0] else ""
            stderr = output[1].decode("utf-8").strip() if output[1] else ""

            return {
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": exit_code,
                "execution_time": time.time() - start_time,
            }

        except Exception as e:
            pooled.is_healthy = False
            return {
                "stdout": "",
                "stderr": str(e),
                "exit_code": 1,
                "execution_time": time.time() - start_time,
            }

    @property
    def stats(self) -> dict:
        """Get pool statistics."""
        return {
            "available": self._available.qsize() if self._available else 0,
            "in_use": len(self._in_use),
            "total_containers": len(self._all_containers),
            "total_created": self._total_created,
            "total_recycled": self._total_recycled,
            "total_executions": self._total_executions,
        }

    def __repr__(self) -> str:
        """String representation of the SandboxPool."""
        available_size = self._available.qsize() if self._available else 0
        return (
            f"<SandboxPool available={available_size} "
            f"in_use={len(self._in_use)} running={self._running}>"
        )
