"""Shared fixtures: fake DNS, a unique spec per test, a recording transport."""

from __future__ import annotations

import socket
import uuid
from collections.abc import Callable

import httpx
import pytest

from core.connectors import ConnectorCapability, ConnectorSpec, CredentialField

PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve every test host to a public address; nothing touches the network."""

    def fake(host: str, port: object, *args: object, **kwargs: object) -> list:
        if host.startswith("internal."):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


@pytest.fixture
def spec() -> ConnectorSpec:
    """A spec with a unique name, so named circuit breakers never leak across tests."""
    return ConnectorSpec(
        name=f"acme_{uuid.uuid4().hex[:8]}",
        display_name="Acme",
        capabilities=frozenset({ConnectorCapability.LOOKUP}),
        credentials=(CredentialField("api_key"),),
        allowed_hosts=frozenset({"api.acme.test", "internal.acme.test"}),
        max_attempts=3,
        retry_base_delay=0.0,
    )


class Recorder:
    """MockTransport handler replaying a scripted sequence of responses."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.requests: list[httpx.Request] = []
        self._responses = list(responses)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = (
            self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        )
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def recorder() -> Callable[..., Recorder]:
    return Recorder
