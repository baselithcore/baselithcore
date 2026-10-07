"""A scriptable legacy-era MCP server speaking newline-delimited JSON on stdio.

Behaviour is chosen by the ``FAKE_MODE`` environment variable so one script
covers every pathological case the client hardening has to survive.
"""

from __future__ import annotations

import json
import os
import sys
import time

MODE = os.environ.get("FAKE_MODE", "basic")


def send(obj: object) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def tool(name: str) -> dict[str, object]:
    return {
        "name": name,
        "description": f"tool {name}",
        "inputSchema": {"type": "object"},
    }


def text_result(rid: object, payload: object) -> None:
    send(
        {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {"content": [{"type": "text", "text": json.dumps(payload)}]},
        }
    )


def list_tools(rid: object, params: dict[str, object]) -> None:
    cursor = params.get("cursor")
    if MODE == "cursor_loop":
        send(
            {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {"tools": [tool("a")], "nextCursor": "same"},
            }
        )
    elif MODE == "infinite_pages":
        n = int(str(cursor or "0")) + 1
        send(
            {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {"tools": [tool(f"t{n}")], "nextCursor": str(n)},
            }
        )
    elif MODE == "pages":
        pages = {None: ("a", "p2"), "p2": ("b", "p3"), "p3": ("c", None)}
        name, nxt = pages[cursor]  # type: ignore[index]
        result: dict[str, object] = {"tools": [tool(name)]}
        if nxt:
            result["nextCursor"] = nxt
        send({"jsonrpc": "2.0", "id": rid, "result": result})
    elif MODE == "slowloris":
        sys.stdout.write("x" * 100)
        sys.stdout.flush()
        time.sleep(30)
    elif MODE == "hugeline":
        sys.stdout.write("x" * (1 << 20) + "\n")
        sys.stdout.flush()
    else:
        send({"jsonrpc": "2.0", "id": rid, "result": {"tools": [tool("echo")]}})


def call_tool(rid: object, params: dict[str, object]) -> None:
    if MODE == "ourid_request":
        # A server REQUEST that reuses the client's request id, then the real
        # reply. The client must answer the request (error) and keep waiting.
        send(
            {
                "jsonrpc": "2.0",
                "id": rid,
                "method": "sampling/createMessage",
                "params": {"messages": []},
            }
        )
        got = json.loads(sys.stdin.readline())
        text_result(rid, {"client_answered": got})
    elif MODE == "sampling":
        send(
            {
                "jsonrpc": "2.0",
                "id": 777,
                "method": "sampling/createMessage",
                "params": {"messages": []},
            }
        )
        got = json.loads(sys.stdin.readline())
        text_result(rid, {"client_answered": got})
    elif MODE == "envdump":
        text_result(rid, sorted(os.environ))
    else:
        text_result(rid, {"ok": True})


for line in sys.stdin:
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        continue
    if "id" not in msg or "method" not in msg:
        continue
    method, rid = msg["method"], msg["id"]
    if method == "initialize":
        send(
            {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "protocolVersion": "2025-11-25",
                    "serverInfo": {"name": "fake", "version": "1"},
                    "capabilities": {"tools": {}},
                },
            }
        )
    elif method == "tools/list":
        list_tools(rid, msg.get("params") or {})
    elif method == "tools/call":
        call_tool(rid, msg.get("params") or {})
    else:
        send(
            {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32601, "message": "Method not found"},
            }
        )
