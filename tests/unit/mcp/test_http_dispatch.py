"""The HTTP transport's request dispatch: what comes back on a single body.

Both HTTP paths run the message through :class:`~core.mcp.dispatch.RequestDispatcher`,
which gives a request its own cancellable task and its progress context. That
same dispatcher installs its sender as the progress channel, so everything a
handler emits — notifications included — arrives through one collector; these
tests pin which of those messages becomes the HTTP body.

Split from ``test_http_transport.py`` to keep both modules under the 500-line
cap.
"""

from __future__ import annotations

from fastapi import FastAPI

from core.mcp.http_transport import (
    PROTOCOL_HEADER,
    SESSION_HEADER,
    create_mcp_http_router,
)
from core.mcp.server import MCPServer

from .test_http_transport import _asgi_client, _config, _initialize_msg


async def test_progress_notification_does_not_replace_the_result():
    """The dispatcher installs its sender as the progress channel, so a handler
    calling report_progress emits a notification through the same collector the
    response arrives on. The response is the one correlated to the request."""
    from core.mcp.progress import report_progress

    server = MCPServer(name="progress-server", version="1.0.0")

    @server.tool(name="chatty", description="Reports progress then returns")
    async def chatty() -> str:
        await report_progress(1, 2, "halfway")
        return "done"

    app = FastAPI()
    app.include_router(create_mcp_http_router(server, config=_config()))

    async with _asgi_client(app) as client:
        init = await client.post("/mcp", json=_initialize_msg())
        session = init.headers[SESSION_HEADER]
        response = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {
                    "name": "chatty",
                    "arguments": {},
                    "_meta": {"progressToken": "t-1"},
                },
            },
            headers={SESSION_HEADER: session, PROTOCOL_HEADER: "2025-06-18"},
        )

    body = response.json()
    assert "method" not in body, f"got a notification instead of the result: {body}"
    assert body["id"] == 7
    assert body["result"]["content"][0]["text"] == "done"


async def test_notification_only_exchange_is_accepted_with_no_body():
    """A message the handler answers with nothing still gets 202, not a
    notification that happened to be emitted alongside it."""
    server = MCPServer(name="quiet-server", version="1.0.0")
    app = FastAPI()
    app.include_router(create_mcp_http_router(server, config=_config()))

    async with _asgi_client(app) as client:
        init = await client.post("/mcp", json=_initialize_msg())
        session = init.headers[SESSION_HEADER]
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={SESSION_HEADER: session, PROTOCOL_HEADER: "2025-06-18"},
        )

    assert response.status_code == 202
    assert response.content == b""
