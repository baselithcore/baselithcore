"""Multi Round-Trip Requests (SEP-2322): asking the client for input.

2026-07-28 removed server-initiated requests. A server that needs elicitation,
sampling or roots returns an ``InputRequiredResult`` and the client retries the
original request carrying the answers. Because the retry is an independent
request, any context the server needs must survive in ``requestState`` — which
travels through the client and is therefore attacker-controlled.
"""

from __future__ import annotations

from typing import Any

import pytest

from core.mcp.errors import MissingRequiredClientCapability
from core.mcp.modern import MODERN_PROTOCOL_VERSION, PROTOCOL_VERSION_KEY
from core.mcp.mrtr import InputRequired, RequestStateSealer, get_input_responses
from core.mcp.server import MCPServer

_ELICIT = {
    "method": "elicitation/create",
    "params": {
        "mode": "form",
        "message": "Please provide your GitHub username",
        "requestedSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
}


def _call(params: dict[str, Any], capabilities: dict[str, Any] | None = None) -> dict:
    body = dict(params)
    body["_meta"] = {
        PROTOCOL_VERSION_KEY: MODERN_PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientCapabilities": (
            {"elicitation": {}} if capabilities is None else capabilities
        ),
    }
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": body}


def _server() -> MCPServer:
    server = MCPServer()

    async def login(repo: str) -> str:
        answers = get_input_responses()
        if "github_login" not in answers:
            raise InputRequired({"github_login": _ELICIT}, state={"repo": repo})
        user = answers["github_login"]["content"]["name"]
        return f"{user}@{repo}"

    server.register_tool(
        name="login",
        description="Needs a GitHub username",
        input_schema={
            "type": "object",
            "properties": {"repo": {"type": "string"}},
            "required": ["repo"],
        },
        handler=login,
    )
    return server


class TestInputRequiredResult:
    @pytest.mark.asyncio
    async def test_handler_can_ask_the_client_for_input(self) -> None:
        response = await _server().handle_message(
            _call({"name": "login", "arguments": {"repo": "acme"}})
        )

        assert response is not None
        result = response["result"]
        assert result["resultType"] == "input_required"
        assert result["inputRequests"]["github_login"] == _ELICIT
        assert isinstance(result["requestState"], str) and result["requestState"]

    @pytest.mark.asyncio
    async def test_retry_with_answers_completes_the_call(self) -> None:
        server = _server()
        first = await server.handle_message(
            _call({"name": "login", "arguments": {"repo": "acme"}})
        )
        assert first is not None
        state = first["result"]["requestState"]

        second = await server.handle_message(
            _call(
                {
                    "name": "login",
                    "arguments": {"repo": "acme"},
                    "inputResponses": {
                        "github_login": {
                            "action": "accept",
                            "content": {"name": "octocat"},
                        }
                    },
                    "requestState": state,
                }
            )
        )

        assert second is not None
        assert second["result"]["resultType"] == "complete"
        assert second["result"]["content"][0]["text"] == "octocat@acme"

    @pytest.mark.asyncio
    async def test_input_requests_need_the_matching_client_capability(self) -> None:
        """A server must never ask for something the client cannot provide."""
        response = await _server().handle_message(
            _call({"name": "login", "arguments": {"repo": "acme"}}, capabilities={})
        )

        assert response is not None
        assert response["error"]["code"] == -32021
        assert "elicitation" in response["error"]["data"]["requiredCapabilities"]

    @pytest.mark.asyncio
    async def test_legacy_clients_get_a_tool_error_instead(self) -> None:
        """MRTR does not exist before 2026-07-28: fail loudly, not silently."""
        response = await _server().handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "login", "arguments": {"repo": "acme"}},
            }
        )

        assert response is not None
        assert response["result"]["isError"] is True


class TestRequestStateSealing:
    """`requestState` passes through the client, so it is attacker-controlled."""

    @staticmethod
    def _sealer() -> RequestStateSealer:
        return RequestStateSealer(secret=b"unit-test-secret", ttl_seconds=60)

    def test_round_trip_returns_the_payload(self) -> None:
        sealer = self._sealer()
        sealed = sealer.seal({"repo": "acme"}, principal="alice", request="tools/call")

        assert sealer.unseal(sealed, principal="alice", request="tools/call") == {
            "repo": "acme"
        }

    def test_tampered_state_is_rejected(self) -> None:
        sealer = self._sealer()
        sealed = sealer.seal({"repo": "acme"}, principal="alice", request="tools/call")
        tampered = sealed[:-2] + ("AA" if not sealed.endswith("AA") else "BB")

        with pytest.raises(ValueError):
            sealer.unseal(tampered, principal="alice", request="tools/call")

    def test_state_is_bound_to_its_principal(self) -> None:
        """Cross-user replay must fail even with an intact signature."""
        sealer = self._sealer()
        sealed = sealer.seal({"repo": "acme"}, principal="alice", request="tools/call")

        with pytest.raises(ValueError):
            sealer.unseal(sealed, principal="mallory", request="tools/call")

    def test_state_is_bound_to_its_originating_request(self) -> None:
        sealer = self._sealer()
        sealed = sealer.seal({"repo": "acme"}, principal="alice", request="tools/call")

        with pytest.raises(ValueError):
            sealer.unseal(sealed, principal="alice", request="prompts/get")

    def test_expired_state_is_rejected(self) -> None:
        sealer = RequestStateSealer(secret=b"unit-test-secret", ttl_seconds=0)
        sealed = sealer.seal({"repo": "acme"}, principal=None, request="tools/call")

        with pytest.raises(ValueError, match="expired"):
            sealer.unseal(sealed, principal=None, request="tools/call")

    @pytest.mark.asyncio
    async def test_forged_state_is_refused_by_the_server(self) -> None:
        response = await _server().handle_message(
            _call(
                {
                    "name": "login",
                    "arguments": {"repo": "acme"},
                    "requestState": "forged",
                    "inputResponses": {},
                }
            )
        )

        assert response is not None
        assert response["error"]["code"] == -32602


class TestMissingCapabilityError:
    def test_error_carries_the_required_capabilities(self) -> None:
        error = MissingRequiredClientCapability(
            "needs elicitation", data={"requiredCapabilities": ["elicitation"]}
        )

        assert error.code == -32021
        assert error.data["requiredCapabilities"] == ["elicitation"]


class TestClientRoundTrip:
    """The client fulfils the ask and retries, transparently to the caller."""

    @staticmethod
    def _client(server: MCPServer, provider: Any) -> Any:
        from core.mcp.client import MCPClient

        client = MCPClient(input_provider=provider)
        client._connected = True
        client._protocol_version = MODERN_PROTOCOL_VERSION
        client._client_info = {"name": "test", "version": "1"}

        async def send(method: str, params: dict[str, Any]) -> dict[str, Any]:
            message = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": {
                    **params,
                    "_meta": {
                        PROTOCOL_VERSION_KEY: MODERN_PROTOCOL_VERSION,
                        "io.modelcontextprotocol/clientCapabilities": (
                            client.client_capabilities
                        ),
                    },
                },
            }
            response = await server.handle_message(message)
            assert response is not None
            if "error" in response:
                raise RuntimeError(response["error"]["message"])
            return response["result"]

        client._send_request = send  # type: ignore[assignment]
        return client

    @pytest.mark.asyncio
    async def test_client_answers_and_retries(self) -> None:
        asked: list[dict[str, Any]] = []

        async def provider(requests: dict[str, Any]) -> dict[str, Any]:
            asked.append(requests)
            return {
                "github_login": {"action": "accept", "content": {"name": "octocat"}}
            }

        client = self._client(_server(), provider)

        assert await client.call_tool("login", {"repo": "acme"}) == "octocat@acme"
        assert list(asked[0]) == ["github_login"]

    @pytest.mark.asyncio
    async def test_without_a_provider_the_ask_is_an_error(self) -> None:
        from core.mcp.client import MCPToolError

        client = self._client(_server(), None)
        client.client_capabilities = {"elicitation": {}}

        with pytest.raises(MCPToolError, match="no input_provider"):
            await client.call_tool("login", {"repo": "acme"})
