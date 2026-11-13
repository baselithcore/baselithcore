"""A factory may resolve another service from the same container.

Regression: ``DependencyContainer`` and ``Scope`` held a plain
``threading.Lock`` across the user's factory call. A service whose factory
asked the container for its own dependency -- the ordinary shape of a
dependency graph, and what the class docstring promises -- re-acquired the
lock it already held and blocked its thread forever, with no error.

Each case runs in a worker thread with a timeout, so a regression fails the
test instead of hanging the suite.
"""

from __future__ import annotations

import contextvars
import threading
from collections.abc import Callable

from baselith.core.di.container import DependencyContainer, ServiceLifetime


class Repository:
    pass


class Service:
    def __init__(self, repository: Repository) -> None:
        self.repository = repository


def _resolve_with_timeout(resolve: Callable[[], Service]) -> Service | None:
    outcome: list[Service] = []
    # A bare thread starts with an empty context, so it would not see the scope
    # the caller is inside; run it in a copy of the caller's context instead.
    context = contextvars.copy_context()
    worker = threading.Thread(
        target=context.run, args=(lambda: outcome.append(resolve()),), daemon=True
    )
    worker.start()
    worker.join(timeout=3.0)
    assert not worker.is_alive(), "nested resolve deadlocked on a container lock"
    return outcome[0] if outcome else None


def _wired(
    service_lifetime: ServiceLifetime, repository_lifetime: ServiceLifetime
) -> DependencyContainer:
    container = DependencyContainer()
    container.register(Repository, Repository, lifetime=repository_lifetime)
    container.register(
        Service,
        lambda: Service(container.resolve(Repository)),
        lifetime=service_lifetime,
    )
    return container


def test_singleton_factory_can_resolve_a_singleton_dependency() -> None:
    container = _wired(ServiceLifetime.SINGLETON, ServiceLifetime.SINGLETON)

    service = _resolve_with_timeout(lambda: container.resolve(Service))

    assert service is not None
    assert service.repository is container.resolve(Repository)


def test_transient_factory_can_resolve_a_transient_dependency() -> None:
    container = _wired(ServiceLifetime.TRANSIENT, ServiceLifetime.TRANSIENT)

    service = _resolve_with_timeout(lambda: container.resolve(Service))

    assert service is not None
    assert isinstance(service.repository, Repository)


def test_scoped_factory_can_resolve_a_scoped_dependency() -> None:
    container = _wired(ServiceLifetime.SCOPED, ServiceLifetime.SCOPED)

    with container.create_scope() as scope:
        service = _resolve_with_timeout(lambda: scope.resolve(Service))
        assert service is not None
        # One scope, one repository: the dependency is shared, not rebuilt.
        assert service.repository is scope.resolve(Repository)
