"""Answering requests a server makes of the client (sampling, elicitation, roots).

Default-deny. A server that asks the client to run an LLM call
(``sampling/createMessage``), prompt the user (``elicitation/create``) or list
filesystem roots (``roots/list``) is asking for the client's authority, so the
client does none of it unless the caller handed over an explicit
``input_provider`` *and* the matching capability is declared. Everything else
is answered with a JSON-RPC error — never silently dropped, which would leave
the server waiting on a request it believes is in flight.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from core.observability.logging import get_logger

logger = get_logger(__name__)

InputProvider = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]

METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603

#: Server-to-client request methods and the client capability each requires.
INPUT_METHODS: dict[str, str] = {
    "sampling/createMessage": "sampling",
    "elicitation/create": "elicitation",
    "roots/list": "roots",
}


def error_response(request_id: Any, code: int, message: str) -> dict[str, Any]:
    """A JSON-RPC error response to a server request."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


async def answer_server_request(
    message: dict[str, Any],
    *,
    input_provider: InputProvider | None,
    capabilities: dict[str, Any],
) -> dict[str, Any]:
    """Build the JSON-RPC response to a request the server sent us."""
    request_id = message.get("id")
    method = message.get("method")
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}

    capability = INPUT_METHODS.get(method) if isinstance(method, str) else None
    if capability is None:
        return error_response(request_id, METHOD_NOT_FOUND, "Method not found")
    if input_provider is None or capability not in capabilities:
        logger.warning("mcp_server_request_denied", method=method)
        return error_response(
            request_id, METHOD_NOT_FOUND, f"{method} is not supported by this client"
        )

    key = str(request_id)
    try:
        answers = await input_provider(
            {key: {"method": method, "params": message.get("params") or {}}}
        )
        result = answers[key]
    except Exception as exc:
        # The provider is the caller's code; its failure is not the server's
        # business, so no detail leaves in the error.
        logger.warning(
            "mcp_input_provider_failed", method=method, error=type(exc).__name__
        )
        return error_response(request_id, INTERNAL_ERROR, "Input request failed")
    if not isinstance(result, dict):
        return error_response(request_id, INTERNAL_ERROR, "Input request failed")
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


__all__ = ["INPUT_METHODS", "InputProvider", "answer_server_request", "error_response"]
