"""A2A 0.3.0 conformance: discovery path, method names, states, card fields.

The card claimed ``protocolVersion: 0.3.0`` while the implementation was still
0.2-shaped in four visible ways — the well-known path, the push-notification
method names, a ``TaskState`` enum missing two members, and a card that
described no security scheme and advertised a non-spec ``protocols`` list.
Each is a place a conformant peer either fails to find the agent or fails to
talk to it.
"""

from __future__ import annotations

import asyncio
import json

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core.a2a.agent_card import AgentCapabilities, AgentCard  # noqa: E402
from core.a2a.protocol import A2AMethod, ErrorCode  # noqa: E402
from core.a2a.router import (  # noqa: E402
    create_a2a_router,
    create_wellknown_router,
)
from core.a2a.server import EchoA2AServer  # noqa: E402
from core.a2a.types import TaskState  # noqa: E402


def _card() -> AgentCard:
    return AgentCard(
        name="test-agent",
        description="A test agent",
        version="9.9.9",
        agentCapabilities=AgentCapabilities(streaming=True),
    )


def _wellknown_client() -> TestClient:
    app = FastAPI()
    app.include_router(create_wellknown_router(_card()))
    return TestClient(app)


def _server_client() -> TestClient:
    app = FastAPI()
    app.include_router(create_a2a_router(EchoA2AServer(_card())))
    return TestClient(app)


# ---------------------------------------------------------------------------
# Discovery path
# ---------------------------------------------------------------------------


class TestDiscoveryPath:
    def test_canonical_0_3_0_path_is_served(self) -> None:
        response = _wellknown_client().get("/.well-known/agent-card.json")

        assert response.status_code == 200
        assert response.json()["name"] == "test-agent"

    def test_legacy_path_stays_an_alias(self) -> None:
        client = _wellknown_client()

        canonical = client.get("/.well-known/agent-card.json").json()
        legacy = client.get("/.well-known/agent.json").json()

        assert legacy == canonical

    def test_full_router_serves_both_paths(self) -> None:
        client = _server_client()

        assert client.get("/.well-known/agent-card.json").status_code == 200
        assert client.get("/.well-known/agent.json").status_code == 200
        assert client.get("/a2a/agent-card").status_code == 200


# ---------------------------------------------------------------------------
# Push-notification configuration methods
# ---------------------------------------------------------------------------


class TestPushNotificationMethods:
    def test_0_3_0_method_names_exist(self) -> None:
        assert (
            A2AMethod.TASKS_PUSH_NOTIFICATION_CONFIG_SET.value
            == "tasks/pushNotificationConfig/set"
        )
        assert (
            A2AMethod.TASKS_PUSH_NOTIFICATION_CONFIG_GET.value
            == "tasks/pushNotificationConfig/get"
        )
        assert (
            A2AMethod.TASKS_PUSH_NOTIFICATION_CONFIG_LIST.value
            == "tasks/pushNotificationConfig/list"
        )
        assert (
            A2AMethod.TASKS_PUSH_NOTIFICATION_CONFIG_DELETE.value
            == "tasks/pushNotificationConfig/delete"
        )

    def test_old_names_survive_as_deprecated_aliases(self) -> None:
        assert (
            A2AMethod.TASKS_PUSH_NOTIFICATION_SET.value == "tasks/pushNotification/set"
        )
        assert (
            A2AMethod.TASKS_PUSH_NOTIFICATION_GET.value == "tasks/pushNotification/get"
        )

    @pytest.mark.parametrize(
        "method",
        [
            "tasks/pushNotificationConfig/set",
            "tasks/pushNotificationConfig/get",
            "tasks/pushNotificationConfig/list",
            "tasks/pushNotificationConfig/delete",
            # Deprecated 0.2 spellings, still answered the same way.
            "tasks/pushNotification/set",
            "tasks/pushNotification/get",
        ],
    )
    async def test_every_spelling_gets_the_spec_error(self, method: str) -> None:
        server = EchoA2AServer(_card())

        response = await server.dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": {}}
        )

        assert response["error"]["code"] == ErrorCode.PUSH_NOTIFICATION_NOT_SUPPORTED

    async def test_an_unrelated_tasks_method_is_still_method_not_found(self) -> None:
        server = EchoA2AServer(_card())

        response = await server.dispatch(
            {"jsonrpc": "2.0", "id": 1, "method": "tasks/invented", "params": {}}
        )

        assert response["error"]["code"] == ErrorCode.METHOD_NOT_FOUND


# ---------------------------------------------------------------------------
# Task states
# ---------------------------------------------------------------------------


class TestTaskStates:
    def test_0_3_0_states_exist(self) -> None:
        assert TaskState.AUTH_REQUIRED.value == "auth-required"
        assert TaskState.UNKNOWN.value == "unknown"

    def test_new_states_round_trip_through_the_wire_value(self) -> None:
        assert TaskState("auth-required") is TaskState.AUTH_REQUIRED
        assert TaskState("unknown") is TaskState.UNKNOWN

    def test_new_states_are_not_terminal(self) -> None:
        from core.a2a.types import Task

        for state in (TaskState.AUTH_REQUIRED, TaskState.UNKNOWN):
            task = Task.create(state=state)
            assert task.is_terminal is False


# ---------------------------------------------------------------------------
# Agent card shape
# ---------------------------------------------------------------------------


class TestAgentCardShape:
    def test_card_declares_its_security_scheme(self) -> None:
        card = _card().to_dict()

        scheme = card["securitySchemes"]["hmacSignature"]
        assert scheme["type"] == "apiKey"
        assert scheme["in"] == "header"
        assert scheme["name"] == "X-A2A-Signature"
        assert card["security"] == [{"hmacSignature": []}]

    def test_card_declares_a_preferred_transport(self) -> None:
        assert _card().to_dict()["preferredTransport"] == "JSONRPC"

    def test_non_spec_protocols_list_is_gone(self) -> None:
        assert "protocols" not in _card().to_dict()

    def test_discovery_matches_a_card_that_only_declares_a_transport(self) -> None:
        """A 0.3.0 peer carries preferredTransport, not the old protocols list."""
        from core.a2a.discovery import AgentDiscovery

        card = _card()
        card.protocols = []
        discovery = AgentDiscovery()
        discovery.register(card)

        assert discovery.find_by_protocol("jsonrpc", healthy_only=False) == [card]

    def test_peer_card_without_a_scheme_is_not_given_ours(self) -> None:
        """Inventing a scheme for a peer tells the caller to sign requests that
        peer will not verify — and puts it back on the wire if re-emitted."""
        peer = AgentCard.from_dict(
            {"name": "peer", "description": "A peer", "version": "1.0.0"}
        )

        assert peer.securitySchemes == {}
        assert peer.security == []
        assert "securitySchemes" not in peer.to_dict()
        assert "security" not in peer.to_dict()

    def test_locally_constructed_cards_keep_the_default_scheme(self) -> None:
        assert "hmacSignature" in AgentCard(name="x", description="y").securitySchemes

    def test_peer_declared_scheme_is_preserved(self) -> None:
        peer = AgentCard.from_dict(
            {
                "name": "peer",
                "description": "A peer",
                "version": "1.0.0",
                "securitySchemes": {"oauth": {"type": "oauth2"}},
                "security": [{"oauth": ["read"]}],
            }
        )

        assert peer.to_dict()["securitySchemes"] == {"oauth": {"type": "oauth2"}}
        assert peer.to_dict()["security"] == [{"oauth": ["read"]}]

    def test_round_trip_preserves_the_new_fields(self) -> None:
        original = _card()
        restored = AgentCard.from_dict(original.to_dict())

        assert restored.to_dict() == original.to_dict()

    def test_overridden_security_scheme_is_emitted_verbatim(self) -> None:
        card = _card()
        card.securitySchemes = {"bearer": {"type": "http", "scheme": "bearer"}}
        card.security = [{"bearer": []}]

        emitted = card.to_dict()

        assert emitted["securitySchemes"] == {
            "bearer": {"type": "http", "scheme": "bearer"}
        }
        assert emitted["security"] == [{"bearer": []}]

    def test_served_card_carries_every_field(self) -> None:
        """A declared response_model must not silently filter the card."""
        served = _wellknown_client().get("/.well-known/agent-card.json").json()

        assert served == _card().to_dict()


# ---------------------------------------------------------------------------
# Streaming endpoint
# ---------------------------------------------------------------------------


class TestStreamKeepalive:
    async def test_quiet_stream_emits_keepalive_comments(self, monkeypatch) -> None:
        """An idle SSE stream that says nothing is dropped by intermediaries."""
        import core.a2a.router as router_module

        monkeypatch.setattr(router_module, "SSE_KEEPALIVE_SECONDS", 0.01)

        started = asyncio.Event()

        async def _slow_events(_body):
            started.set()
            await asyncio.sleep(0.05)
            yield {"jsonrpc": "2.0", "id": 1, "result": {"final": True}}

        frames = []
        async for frame in router_module.sse_frames(_slow_events(None)):
            frames.append(frame)

        assert any(frame.startswith(":") for frame in frames)
        assert json.loads(frames[-1].removeprefix("data: ").strip())["id"] == 1

    async def test_producer_is_cancelled_when_the_consumer_leaves(self) -> None:
        """Client gone: the work behind the stream must not keep running."""
        import core.a2a.router as router_module

        cancelled = asyncio.Event()

        async def _endless(_body):
            try:
                while True:
                    yield {"jsonrpc": "2.0", "id": 1, "result": {}}
                    await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        stream = router_module.sse_frames(_endless(None))
        assert await anext(stream)
        await stream.aclose()
        await asyncio.sleep(0.02)

        assert cancelled.is_set()

    async def test_producer_waits_once_the_buffer_is_full(self) -> None:
        """Backpressure: an agent emitting faster than the peer reads must be
        made to wait, not buffered without limit."""
        import core.a2a.router as router_module

        emitted = 0

        async def _firehose(_body):
            nonlocal emitted
            while True:
                emitted += 1
                yield {"jsonrpc": "2.0", "id": emitted, "result": {}}

        stream = router_module.sse_frames(_firehose(None))
        assert await anext(stream)
        # Let the producer run unimpeded for a while; the bound is what stops it.
        for _ in range(20):
            await asyncio.sleep(0)

        assert emitted <= router_module._STREAM_BUFFER + 2, (
            f"producer ran unbounded: {emitted} events queued"
        )
        await stream.aclose()

    async def test_every_event_survives_the_bounded_queue(self) -> None:
        """Backpressure must slow the producer, never drop an event."""
        import core.a2a.router as router_module

        total = router_module._STREAM_BUFFER * 3

        async def _many(_body):
            for index in range(total):
                yield {"jsonrpc": "2.0", "id": index, "result": {}}

        seen = [
            json.loads(frame.removeprefix("data: "))["id"]
            async for frame in router_module.sse_frames(_many(None))
            if frame.startswith("data: ")
        ]

        assert seen == list(range(total))

    def test_message_stream_is_served_as_sse(self) -> None:
        client = _server_client()

        response = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": A2AMethod.MESSAGE_STREAM.value,
                "params": {
                    "message": {
                        "role": "user",
                        "parts": [{"kind": "text", "text": "hi"}],
                    }
                },
            },
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        payloads = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert payloads[-1]["result"]["final"] is True


class TestTypedResponses:
    def test_health_is_typed_in_the_schema(self) -> None:
        app = FastAPI()
        app.include_router(create_a2a_router(EchoA2AServer(_card())))
        schema = app.openapi()

        health = schema["paths"]["/a2a/health"]["get"]["responses"]["200"]
        assert "$ref" in health["content"]["application/json"]["schema"]

    def test_dispatch_declares_a_jsonrpc_response(self) -> None:
        app = FastAPI()
        app.include_router(create_a2a_router(EchoA2AServer(_card())))
        schema = app.openapi()

        dispatch = schema["paths"]["/a2a"]["post"]["responses"]["200"]
        assert "$ref" in dispatch["content"]["application/json"]["schema"]

    def test_health_still_answers(self) -> None:
        response = _server_client().get("/a2a/health")

        assert response.json() == {
            "status": "healthy",
            "agent": "test-agent",
            "version": "9.9.9",
        }
