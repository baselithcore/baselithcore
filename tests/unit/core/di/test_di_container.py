import pytest
from typing import Protocol, runtime_checkable
from core.di.container import (
    DependencyContainer,
    ServiceLifetime,
    ServiceNotFoundError,
    ScopeNotActiveError,
    ServiceRegistry,
)


@runtime_checkable
class IService(Protocol):
    def work(self) -> str: ...


class MockService:
    def __init__(self, value: str = "default"):
        self.value = value

    def work(self) -> str:
        return self.value


class TestDependencyContainer:
    @pytest.fixture
    def container(self):
        return DependencyContainer()

    def test_singleton_lifetime(self, container):
        count = 0

        def factory():
            nonlocal count
            count += 1
            return MockService(f"v{count}")

        container.register(IService, factory, lifetime=ServiceLifetime.SINGLETON)

        s1 = container.resolve(IService)
        s2 = container.resolve(IService)

        assert s1 is s2
        assert s1.work() == "v1"
        assert count == 1

    def test_transient_lifetime(self, container):
        count = 0

        def factory():
            nonlocal count
            count += 1
            return MockService(f"v{count}")

        container.register(IService, factory, lifetime=ServiceLifetime.TRANSIENT)

        s1 = container.resolve(IService)
        s2 = container.resolve(IService)

        assert s1 is not s2
        assert s1.work() == "v1"
        assert s2.work() == "v2"
        assert count == 2

    @pytest.mark.asyncio
    async def test_scoped_lifetime(self, container):
        count = 0

        def factory():
            nonlocal count
            count += 1
            return MockService(f"v{count}")

        container.register(IService, factory, lifetime=ServiceLifetime.SCOPED)

        # Resolve outside scope should fail
        with pytest.raises(ScopeNotActiveError):
            container.resolve(IService)

        # First scope
        async with container.create_scope() as scope:
            s1 = scope.resolve(IService)
            s2 = scope.resolve(IService)
            assert s1 is s2
            assert s1.work() == "v1"

        # Second scope
        async with container.create_scope() as scope2:
            s3 = scope2.resolve(IService)
            assert s3 is not s1
            assert s3.work() == "v2"

    def test_register_instance(self, container):
        instance = MockService("fixed")
        container.register_instance(IService, instance)

        s1 = container.resolve(IService)
        assert s1 is instance

    def test_service_not_found(self, container):
        with pytest.raises(ServiceNotFoundError):
            container.resolve(IService)

    def test_container_clear(self, container):
        container.register_instance(IService, MockService())
        assert container.has(IService)
        container.clear()
        assert not container.has(IService)


class TestServiceRegistry:
    def setup_method(self):
        ServiceRegistry.clear()

    def test_registry_basic(self):
        instance = MockService("reg")
        ServiceRegistry.register(IService, instance)
        assert ServiceRegistry.has(IService)
        assert ServiceRegistry.get(IService) is instance

    def test_registry_not_found(self):
        with pytest.raises(ServiceNotFoundError):
            ServiceRegistry.get(IService)
