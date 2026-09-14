"""Server-side limits on ``tools/call``: timeout, result size, error text.

Three holes this pins shut:

* a tool handler that never returns held the request (and its connection) for
  as long as it liked — there was no server-side deadline at all;
* a handler returning an unbounded blob was serialized verbatim into the
  result, so one call could exhaust the client's context (and the process's
  memory);
* a handler exception was formatted into the result the model reads, which is
  how internal paths, DSNs and driver internals reach an external client.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from core.mcp.server import MCPServer


def _config(**overrides):
    base = {
        "mcp_tool_call_timeout_seconds": 60.0,
        "mcp_max_tool_result_bytes": 1024 * 1024,
        "mcp_task_ttl_ms": 3_600_000,
        "mcp_task_poll_interval_ms": 1000,
        "mcp_cache_ttl_ms": 60000,
        "mcp_cache_scope": "private",
        "mcp_list_page_size": 100,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _server(**config_overrides) -> MCPServer:
    server = MCPServer(name="limits", version="1.0.0")
    server.config = _config(**config_overrides)
    return server


def _text(result: dict) -> str:
    return "".join(
        block.get("text", "")
        for block in result.get("content", [])
        if block.get("type") == "text"
    )


class TestToolCallTimeout:
    async def test_hung_handler_is_cut_off_at_the_configured_deadline(self) -> None:
        server = _server(mcp_tool_call_timeout_seconds=0.05)

        @server.tool(name="hang", description="Never returns")
        async def hang() -> str:
            await asyncio.sleep(30)
            return "never"

        result = await server._handle_call_tool({"name": "hang", "arguments": {}})

        assert result["isError"] is True
        assert "timed out" in _text(result).lower()

    async def test_timeout_does_not_leak_into_a_slower_tool(self) -> None:
        server = _server(mcp_tool_call_timeout_seconds=5.0)

        @server.tool(name="quick", description="Returns promptly")
        async def quick() -> str:
            await asyncio.sleep(0)
            return "done"

        result = await server._handle_call_tool({"name": "quick", "arguments": {}})

        assert result["isError"] is False
        assert _text(result) == "done"

    async def test_a_tools_own_timeout_is_not_blamed_on_the_deadline(self) -> None:
        """A handler raising TimeoutError itself (an upstream HTTP read, say)
        arrives at the same boundary as our wait_for breach. Reporting it as
        "the server cut you off after 60s" sends the operator hunting for a
        deadline that never fired."""
        server = _server(mcp_tool_call_timeout_seconds=60.0)

        @server.tool(name="upstream", description="Upstream read times out")
        async def upstream() -> str:
            raise TimeoutError("read timed out connecting to vendor API")

        result = await server._handle_call_tool({"name": "upstream", "arguments": {}})

        assert result["isError"] is True
        message = _text(result)
        assert "timed out after" not in message
        assert "vendor API" not in message
        assert "error id" in message.lower()

    async def test_a_tools_own_timeout_is_opaque_without_a_deadline(self) -> None:
        server = _server(mcp_tool_call_timeout_seconds=0)

        @server.tool(name="upstream2", description="Upstream read times out")
        async def upstream2() -> str:
            raise TimeoutError("read timed out connecting to vendor API")

        result = await server._handle_call_tool({"name": "upstream2", "arguments": {}})

        assert result["isError"] is True
        assert "timed out after" not in _text(result)
        assert "error id" in _text(result).lower()

    async def test_zero_timeout_disables_the_deadline(self) -> None:
        server = _server(mcp_tool_call_timeout_seconds=0)

        @server.tool(name="ok", description="Returns")
        async def ok() -> str:
            return "fine"

        result = await server._handle_call_tool({"name": "ok", "arguments": {}})

        assert result["isError"] is False


class TestResultSizeCap:
    async def test_oversized_text_result_is_truncated_with_a_notice(self) -> None:
        server = _server(mcp_max_tool_result_bytes=2048)

        @server.tool(name="flood", description="Returns a blob")
        async def flood() -> str:
            return "x" * 100_000

        result = await server._handle_call_tool({"name": "flood", "arguments": {}})

        assert len(json.dumps(result).encode()) <= 2048
        # A truncated result is not an execution failure: the model gets what
        # fit plus an explicit notice that the rest was dropped.
        assert result["isError"] is False
        assert result["_meta"]["truncated"] is True
        assert result["_meta"]["originalBytes"] > 2048
        assert "truncated" in _text(result).lower()

    async def test_structured_content_is_dropped_when_truncating(self) -> None:
        """A truncated object would violate the schema the tool promised."""
        server = _server(mcp_max_tool_result_bytes=2048)

        @server.tool(
            name="structured",
            description="Returns structured output",
            output_schema={
                "type": "object",
                "properties": {"blob": {"type": "string"}},
                "required": ["blob"],
            },
        )
        async def structured() -> dict:
            return {"blob": "y" * 100_000}

        result = await server._handle_call_tool({"name": "structured", "arguments": {}})

        assert "structuredContent" not in result
        assert result["_meta"]["truncated"] is True

    async def test_result_within_the_cap_is_untouched(self) -> None:
        server = _server(mcp_max_tool_result_bytes=1024 * 1024)

        @server.tool(name="small", description="Small result")
        async def small() -> str:
            return "hello"

        result = await server._handle_call_tool({"name": "small", "arguments": {}})

        assert result == {
            "content": [{"type": "text", "text": "hello"}],
            "isError": False,
        }

    async def test_non_ascii_result_respects_the_cap(self) -> None:
        """The cap is measured on the serialized form, not on raw UTF-8 bytes:
        `json.dumps` escapes a non-ASCII character to six bytes (\\uXXXX), so
        budgeting in raw bytes overshoots the cap several times over."""
        server = _server(mcp_max_tool_result_bytes=2048)

        @server.tool(name="cyrillic", description="Returns a non-ASCII blob")
        async def cyrillic() -> str:
            return "\u044f" * 50_000

        result = await server._handle_call_tool({"name": "cyrillic", "arguments": {}})

        assert len(json.dumps(result).encode()) <= 2048
        assert result["_meta"]["truncated"] is True

    async def test_quote_heavy_result_respects_the_cap(self) -> None:
        """Every quote and backslash costs an extra byte once escaped."""
        server = _server(mcp_max_tool_result_bytes=2048)

        @server.tool(name="quoted", description="Returns quotes and backslashes")
        async def quoted() -> str:
            return '"\\' * 50_000

        result = await server._handle_call_tool({"name": "quoted", "arguments": {}})

        assert len(json.dumps(result).encode()) <= 2048
        assert result["_meta"]["truncated"] is True

    async def test_truncated_text_is_still_the_original_prefix(self) -> None:
        server = _server(mcp_max_tool_result_bytes=2048)

        @server.tool(name="prefix", description="Returns a long blob")
        async def prefix() -> str:
            return "abcdefghij" * 10_000

        result = await server._handle_call_tool({"name": "prefix", "arguments": {}})

        kept = _text(result).split("\n\n[truncated")[0]
        assert kept and ("abcdefghij" * 10_000).startswith(kept)

    async def test_zero_cap_disables_truncation(self) -> None:
        server = _server(mcp_max_tool_result_bytes=0)

        @server.tool(name="big", description="Returns a blob")
        async def big() -> str:
            return "z" * 20_000

        result = await server._handle_call_tool({"name": "big", "arguments": {}})

        assert "_meta" not in result
        assert len(_text(result)) == 20_000


class TestHandlerErrorText:
    async def test_exception_text_never_reaches_the_model(self) -> None:
        server = _server()

        @server.tool(name="boom", description="Raises")
        async def boom() -> str:
            raise RuntimeError("connection to postgres://user:pw@db/app refused")

        result = await server._handle_call_tool({"name": "boom", "arguments": {}})

        assert result["isError"] is True
        message = _text(result)
        assert "postgres" not in message
        assert "RuntimeError" not in message
        # The correlation id is what ties the generic message to the log line
        # that does carry the full text.
        assert "error id" in message.lower()

    async def test_correlation_id_is_logged_with_the_full_text(
        self, monkeypatch
    ) -> None:
        import core.mcp.tool_handlers as tool_handlers

        records: list[tuple[str, dict]] = []

        class _Recorder:
            def warning(self, event, **fields):
                records.append((event, fields))

            def __getattr__(self, _name):
                return lambda *a, **k: None

        monkeypatch.setattr(tool_handlers, "logger", _Recorder())
        server = _server()

        @server.tool(name="boom2", description="Raises")
        async def boom2() -> str:
            raise RuntimeError("secret-internal-detail")

        result = await server._handle_call_tool({"name": "boom2", "arguments": {}})

        failure = next(f for e, f in records if e == "mcp_tool_execution_failed")
        assert failure["error"] == "secret-internal-detail"
        assert failure["error_type"] == "RuntimeError"
        # Same id on both sides, so an operator can join the two.
        assert failure["correlation_id"] in _text(result)
        assert result["isError"] is True

    async def test_input_validation_message_is_still_actionable(self) -> None:
        """SEP-1303: argument errors are the model's to fix, so they stay verbatim."""
        server = _server()

        @server.tool(
            name="typed",
            description="Needs a string",
            input_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        )
        async def typed(name: str) -> str:
            return name

        result = await server._handle_call_tool({"name": "typed", "arguments": {}})

        assert result["isError"] is True
        assert "name" in _text(result)


@pytest.mark.parametrize("timeout", [0.05])
async def test_task_runner_applies_the_same_deadline(timeout: float) -> None:
    """The long-running (tasks extension) path must not bypass the deadline."""
    server = _server(mcp_tool_call_timeout_seconds=timeout)

    @server.tool(name="slow", description="Never returns")
    async def slow() -> str:
        await asyncio.sleep(30)
        return "never"

    runner = server._task_runner(server._tools["slow"], {})
    result = await runner({})

    assert result["isError"] is True
    assert "timed out" in _text(result).lower()
