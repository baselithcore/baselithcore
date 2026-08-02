"""Prompts, icon metadata and argument completion.

Prompts are the third MCP server primitive alongside tools and resources;
`completion/complete` is what makes their arguments usable interactively, and
icons (SEP-973) are the 2025-11-25 display metadata for every primitive.
"""

from __future__ import annotations

from typing import Any

import pytest

from core.mcp.server import MCPServer

_ICON = {"src": "https://example.com/icon.png", "mimeType": "image/png"}


def _prompt_server() -> MCPServer:
    server = MCPServer()

    async def review(language: str, strictness: str = "normal") -> str:
        return f"Review this {language} code, {strictness} mode."

    server.register_prompt(
        name="code_review",
        description="Ask for a code review",
        arguments=[
            {"name": "language", "description": "Source language", "required": True},
            {"name": "strictness", "description": "How picky", "required": False},
        ],
        handler=review,
        completions={"language": ["python", "pytest-plugin", "rust"]},
        icons=[_ICON],
    )
    return server


class TestPromptsCapability:
    @pytest.mark.asyncio
    async def test_prompts_capability_follows_registration(self) -> None:
        """A server with prompts advertises them; an empty one does not."""
        empty = await MCPServer()._handle_initialize({})
        assert "prompts" not in empty["capabilities"]

        with_prompts = await _prompt_server()._handle_initialize({})
        assert with_prompts["capabilities"]["prompts"] == {}
        assert with_prompts["capabilities"]["completions"] == {}


class TestPromptsList:
    @pytest.mark.asyncio
    async def test_list_exposes_arguments(self) -> None:
        result = await _prompt_server()._handle_list_prompts({})

        prompt = result["prompts"][0]
        assert prompt["name"] == "code_review"
        assert prompt["arguments"][0] == {
            "name": "language",
            "description": "Source language",
            "required": True,
        }

    @pytest.mark.asyncio
    async def test_list_is_paginated(self) -> None:
        server = MCPServer()
        for index in range(3):

            async def handler(_i: int = index) -> str:
                return str(_i)

            server.register_prompt(
                name=f"p{index}", description="", arguments=[], handler=handler
            )
        from types import SimpleNamespace

        server.config = SimpleNamespace(mcp_list_page_size=2)

        first = await server._handle_list_prompts({})
        assert len(first["prompts"]) == 2
        second = await server._handle_list_prompts({"cursor": first["nextCursor"]})
        assert len(second["prompts"]) == 1


class TestPromptsGet:
    @pytest.mark.asyncio
    async def test_get_renders_messages(self) -> None:
        result = await _prompt_server()._handle_get_prompt(
            {"name": "code_review", "arguments": {"language": "Python"}}
        )

        assert result["description"] == "Ask for a code review"
        message = result["messages"][0]
        assert message["role"] == "user"
        assert message["content"] == {
            "type": "text",
            "text": "Review this Python code, normal mode.",
        }

    @pytest.mark.asyncio
    async def test_handler_may_return_explicit_messages(self) -> None:
        server = MCPServer()

        async def multi() -> list[dict[str, Any]]:
            return [
                {"role": "user", "content": {"type": "text", "text": "hi"}},
                {"role": "assistant", "content": {"type": "text", "text": "hello"}},
            ]

        server.register_prompt(name="chat", description="", arguments=[], handler=multi)

        result = await server._handle_get_prompt({"name": "chat", "arguments": {}})

        assert [m["role"] for m in result["messages"]] == ["user", "assistant"]

    @pytest.mark.asyncio
    async def test_missing_required_argument_is_invalid_params(self) -> None:
        response = await _prompt_server().handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "prompts/get",
                "params": {"name": "code_review", "arguments": {}},
            }
        )

        assert response is not None
        assert response["error"]["code"] == -32602
        assert "language" in response["error"]["message"]

    @pytest.mark.asyncio
    async def test_unknown_prompt_is_invalid_params(self) -> None:
        response = await _prompt_server().handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "prompts/get",
                "params": {"name": "nope", "arguments": {}},
            }
        )

        assert response is not None
        assert response["error"]["code"] == -32602


class TestIcons:
    """SEP-973: primitives may carry display icons."""

    @pytest.mark.asyncio
    async def test_prompt_icons_are_listed(self) -> None:
        result = await _prompt_server()._handle_list_prompts({})

        assert result["prompts"][0]["icons"] == [_ICON]

    @pytest.mark.asyncio
    async def test_tool_icons_are_listed_and_optional(self) -> None:
        server = MCPServer()

        async def plain() -> str:
            return "ok"

        server.register_tool(
            name="with_icon",
            description="",
            input_schema={"type": "object", "properties": {}},
            handler=plain,
            icons=[_ICON],
        )
        server.register_tool(
            name="no_icon",
            description="",
            input_schema={"type": "object", "properties": {}},
            handler=plain,
        )

        tools = {t["name"]: t for t in (await server._handle_list_tools({}))["tools"]}

        assert tools["with_icon"]["icons"] == [_ICON]
        assert "icons" not in tools["no_icon"]

    @pytest.mark.asyncio
    async def test_resource_icons_are_listed(self) -> None:
        server = MCPServer()

        async def read(uri: str) -> str:
            return uri

        server.register_resource(
            uri="mcp://doc",
            name="Doc",
            description="",
            handler=read,
            icons=[_ICON],
        )

        result = await server._handle_list_resources({})

        assert result["resources"][0]["icons"] == [_ICON]


class TestCompletion:
    """`completion/complete` suggests values for a prompt or template argument."""

    @pytest.mark.asyncio
    async def test_prompt_argument_values_are_prefix_filtered(self) -> None:
        result = await _prompt_server()._handle_complete(
            {
                "ref": {"type": "ref/prompt", "name": "code_review"},
                "argument": {"name": "language", "value": "py"},
            }
        )

        assert result["completion"]["values"] == ["python", "pytest-plugin"]
        assert result["completion"]["total"] == 2
        assert result["completion"]["hasMore"] is False

    @pytest.mark.asyncio
    async def test_argument_without_provider_returns_empty(self) -> None:
        result = await _prompt_server()._handle_complete(
            {
                "ref": {"type": "ref/prompt", "name": "code_review"},
                "argument": {"name": "strictness", "value": ""},
            }
        )

        assert result["completion"]["values"] == []

    @pytest.mark.asyncio
    async def test_resource_template_variable_completion(self) -> None:
        server = MCPServer()

        async def read_report(uri: str, year: str) -> str:
            return uri

        server.register_resource_template(
            uri_template="mcp://reports/{year}",
            name="Reports",
            description="",
            handler=read_report,
            completions={"year": ["2024", "2025", "2026"]},
        )

        result = await server._handle_complete(
            {
                "ref": {"type": "ref/resource", "uri": "mcp://reports/{year}"},
                "argument": {"name": "year", "value": "202"},
            }
        )

        assert result["completion"]["values"] == ["2024", "2025", "2026"]

    @pytest.mark.asyncio
    async def test_callable_provider_receives_the_partial_value(self) -> None:
        server = MCPServer()
        seen: list[str] = []

        async def handler(city: str) -> str:
            return city

        def suggest(partial: str) -> list[str]:
            seen.append(partial)
            return [f"{partial}-suggested"]

        server.register_prompt(
            name="travel",
            description="",
            arguments=[{"name": "city", "required": True}],
            handler=handler,
            completions={"city": suggest},
        )

        result = await server._handle_complete(
            {
                "ref": {"type": "ref/prompt", "name": "travel"},
                "argument": {"name": "city", "value": "Rom"},
            }
        )

        assert seen == ["Rom"]
        assert result["completion"]["values"] == ["Rom-suggested"]

    @pytest.mark.asyncio
    async def test_values_are_capped_at_one_hundred(self) -> None:
        """The spec bounds a completion response to 100 values."""
        server = MCPServer()

        async def handler(n: str) -> str:
            return n

        server.register_prompt(
            name="many",
            description="",
            arguments=[{"name": "n", "required": True}],
            handler=handler,
            completions={"n": [f"v{i:03d}" for i in range(250)]},
        )

        result = await server._handle_complete(
            {
                "ref": {"type": "ref/prompt", "name": "many"},
                "argument": {"name": "n", "value": "v"},
            }
        )

        assert len(result["completion"]["values"]) == 100
        assert result["completion"]["total"] == 250
        assert result["completion"]["hasMore"] is True

    @pytest.mark.asyncio
    async def test_unknown_reference_is_invalid_params(self) -> None:
        response = await _prompt_server().handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "completion/complete",
                "params": {
                    "ref": {"type": "ref/prompt", "name": "nope"},
                    "argument": {"name": "language", "value": ""},
                },
            }
        )

        assert response is not None
        assert response["error"]["code"] == -32602
