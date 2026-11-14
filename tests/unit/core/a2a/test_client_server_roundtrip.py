"""The A2A client must be able to talk to the A2A server this package ships.

Regression: ``A2AClient`` posted a legacy ``A2AMessage`` to ``{endpoint}/a2a/invoke``,
a route the router never exposed -- the router serves JSON-RPC 2.0 on
``POST {endpoint}/a2a``. Every client unit test stubbed the transport, so the
two halves of the package were never run against each other and the mismatch
shipped. These tests close the loop: the real client, the real router, nothing
mocked in between except the network hop (an in-process ASGI transport).
"""

from __future__ import annotations

import httpx
import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402

from baselith.core.a2a.agent_card import AgentCard  # noqa: E402
from baselith.core.a2a.client import A2AClient  # noqa: E402
from baselith.core.a2a.protocol import A2AMethod, ErrorCode  # noqa: E402
from baselith.core.a2a.router import create_a2a_router  # noqa: E402
from baselith.core.a2a.server import EchoA2AServer  # noqa: E402
from baselith.core.a2a.types import Message  # noqa: E402

_PEER = "http://peer.test"
_SECRET = "roundtrip-shared-secret-0123456789abcdef"

_A2A_ENV = (
    "BASELITH_A2A_SHARED_SECRET",
    "BASELITH_A2A_PEER_SECRETS",
    "BASELITH_A2A_PEER_ID",
    "BASELITH_A2A_ALLOW_UNAUTHENTICATED",
    "BASELITH_A2A_ALLOW_LEGACY_NONCELESS",
)


@pytest.fixture(autouse=True)
def _clean_a2a_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # ``config/env.py`` loads the repo-root ``.env`` at import, so a developer's
    # signing secret would otherwise leak into the unsigned case.
    for name in _A2A_ENV:
        monkeypatch.delenv(name, raising=False)


def _connected_client() -> A2AClient:
    card = AgentCard(name="echo", description="Echo agent", endpoint=_PEER)
    app = FastAPI()
    app.include_router(create_a2a_router(EchoA2AServer(card)))
    client = A2AClient(card)
    # Skip ``connect()``: it builds the SSRF-hardened real-network client.
    client._client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=_PEER
    )
    return client


def _hello() -> dict[str, object]:
    return {"message": Message.user_message("hello").to_dict()}


async def test_invoke_message_send_reaches_the_router_unsigned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BASELITH_A2A_ALLOW_UNAUTHENTICATED", "true")
    client = _connected_client()

    response = await client.invoke(A2AMethod.MESSAGE_SEND.value, _hello())

    assert response.success, response.error_message
    assert response.result["status"]["state"] == "completed"
    assert "Echo: hello" in str(response.result)


async def test_invoke_message_send_reaches_the_router_signed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BASELITH_A2A_SHARED_SECRET", _SECRET)
    client = _connected_client()

    response = await client.invoke(A2AMethod.MESSAGE_SEND.value, _hello())

    assert response.success, response.error_message
    assert response.result["status"]["state"] == "completed"


async def test_jsonrpc_error_surfaces_as_a_failed_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BASELITH_A2A_ALLOW_UNAUTHENTICATED", "true")
    client = _connected_client()

    response = await client.invoke("no/such/method", {})

    assert not response.success
    assert response.error_code == ErrorCode.METHOD_NOT_FOUND
    assert "no/such/method" in (response.error_message or "")
