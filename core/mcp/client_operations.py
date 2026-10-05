"""Tool, resource and prompt operations for :class:`~core.mcp.client.MCPClient`.

The wire calls a caller actually makes. Two behaviours live here rather than in
the transport because they are protocol semantics, not framing: a
``tools/call`` that comes back ``isError`` is raised rather than returned, and
an ``InputRequiredResult`` is answered and retried instead of surfacing as a
failure.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

from core.mcp.client_errors import MCPToolError, error_text
from core.mcp.client_types import MCPToolInfo
from core.observability.logging import get_logger

logger = get_logger(__name__)


def _scan_part(part: Any, source: str) -> Any:
    """Scan one content part's text, returning a copy when there is any."""
    from core.guardrails import scan_external_content

    if not isinstance(part, dict):
        return part
    scanned = dict(part)
    text = part.get("text")
    if isinstance(text, str):
        scanned["text"] = scan_external_content(text, source=source)
    resource = part.get("resource")
    # An embedded resource carries its text one level down.
    if isinstance(resource, dict) and isinstance(resource.get("text"), str):
        scanned["resource"] = {
            **resource,
            "text": scan_external_content(resource["text"], source=source),
        }
    return scanned


def scan_content_parts(parts: Any, *, source: str) -> Any:
    """Scan every text-bearing part of an MCP ``content``/``contents`` list.

    Only a lone text item used to be scanned, so a server that answered with
    two text parts — or a text part beside an image — handed its text to the
    model unscanned. Every ``text`` field is now scanned with
    :func:`~core.guardrails.scan_external_content` (and sanitized under the
    ``BASELITH_SANITIZE_EXTERNAL_CONTENT`` policy), including the text of an
    embedded ``resource`` part. Binary parts (``image``, ``audio``, ``blob``)
    pass through untouched. The input is never mutated.

    Args:
        parts: The ``content`` (tool result) or ``contents`` (resource read)
            list; anything that is not a list is returned unchanged.
        source: Origin label recorded with any finding.

    Returns:
        A new list with scanned text, or ``parts`` itself when not a list.
    """
    if not isinstance(parts, list):
        return parts
    return [_scan_part(part, source) for part in parts]


#: Bounds for walking a ``structuredContent`` payload recursively. Past either
#: one the remaining subtree is first scanned as one joined block of its
#: strings (detection: one scan, no recursion), so an adversarially deep or
#: wide payload costs one scan, not a stack overflow — and is still never
#: handed over unscanned.
STRUCTURED_SCAN_MAX_DEPTH = 32
STRUCTURED_SCAN_MAX_NODES = 10_000


def _iter_strings(value: Any) -> Iterator[str]:
    """Yield every string leaf and string key of *value*, iteratively."""
    stack: list[Any] = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            yield node
        elif isinstance(node, dict):
            for key, item in node.items():
                if isinstance(key, str):
                    yield key
                stack.append(item)
        elif isinstance(node, (list, tuple)):
            stack.extend(node)


def _scan_key(
    key: Any,
    original: dict[Any, Any],
    out: dict[Any, Any],
    suffixes: dict[str, int],
    **kw: Any,
) -> Any:
    """Scan one object key; disambiguate when the sanitized one collides.

    Sanitizing can map two distinct keys to the same text (``"a\u200b"`` and
    ``"a"``); writing both under one key would silently drop a value. Keeping
    the *original* key instead would hand the flagged, unsanitized text to the
    model — an attacker could force exactly that by sending the clean twin of
    a poisoned key. So the sanitized key gets a numbered suffix that is free
    in both the input and the output built so far: no value is lost and no
    unsanitized key ever leaves. ``suffixes`` (one dict per object) remembers
    the next number to try for each sanitized key, so many colliding keys
    cost linear, not quadratic, work.
    """
    from core.guardrails import scan_external_content

    if not isinstance(key, str):
        return key
    scanned = scan_external_content(key, **kw)
    if scanned == key or (scanned not in original and scanned not in out):
        return scanned
    logger.warning("mcp_structured_key_sanitize_collision source=%s", kw.get("source"))
    n = suffixes.get(scanned, 2)
    while f"{scanned} ({n})" in original or f"{scanned} ({n})" in out:
        n += 1
    suffixes[scanned] = n + 1
    return f"{scanned} ({n})"


def _sanitize_iteratively(value: Any, source: str) -> Any:
    """Copy *value* with every string leaf and key sanitized; no recursion.

    Container types are preserved (a tuple stays a tuple), so the payload's
    shape never changes however deep it is. Only flagged strings change.
    """
    from core.guardrails import scan_external_content

    kw: dict[str, Any] = {"source": source, "sanitize": True}

    def fresh(node: Any) -> Any:
        if isinstance(node, str):
            return scan_external_content(node, **kw)
        if isinstance(node, dict):
            return {}
        if isinstance(node, (list, tuple)):
            return []
        return node

    root = fresh(value)
    # (source container, its copy); tuples are filled as lists, frozen below.
    stack: list[tuple[Any, Any]] = []
    tuples: list[tuple[Any, Any, list[Any]]] = []  # (holder, slot, list)
    if isinstance(value, (dict, list, tuple)):
        stack.append((value, root))
    while stack:
        src, dst = stack.pop()
        suffixes: dict[str, int] = {}
        items = (
            ((_scan_key(k, src, dst, suffixes, **kw), v) for k, v in src.items())
            if isinstance(src, dict)
            else enumerate(src)
        )
        for slot, item in items:
            copy = fresh(item)
            if isinstance(dst, dict):
                dst[slot] = copy
            else:
                dst.append(copy)
            if isinstance(item, (dict, list, tuple)):
                stack.append((item, copy))
                if isinstance(item, tuple):
                    tuples.append((dst, slot, copy))
    # Children were recorded after their parents: freeze innermost first.
    for holder, slot, lst in reversed(tuples):
        holder[slot] = tuple(lst)
    return tuple(root) if isinstance(value, tuple) else root


def _scan_subtree_as_block(value: Any, source: str) -> Any:
    """Scan an over-bound subtree without recursion; never change its shape.

    Detection always runs, once, over the subtree's strings joined into one
    block. Only when that scan changed the block — it was flagged *and* the
    ``BASELITH_SANITIZE_EXTERNAL_CONTENT`` policy sanitizes — is the subtree
    copied with each flagged string leaf (and key) sanitized in place, via an
    explicit stack. Log-only mode keeps the original value byte for byte.
    """
    from core.guardrails import scan_external_content

    block = "\n".join(_iter_strings(value))
    if scan_external_content(block, source=source) == block:
        return value
    return _sanitize_iteratively(value, source)


def _scan_structured(value: Any, source: str, depth: int, budget: list[int]) -> Any:
    """Return *value* with every string leaf (and key) scanned."""
    from core.guardrails import scan_external_content

    if isinstance(value, str):
        return scan_external_content(value, source=source)
    if not isinstance(value, (dict, list, tuple)):
        return value  # numbers, booleans, null: untouched
    if depth >= STRUCTURED_SCAN_MAX_DEPTH or budget[0] <= 0:
        return _scan_subtree_as_block(value, source)
    budget[0] -= 1
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        suffixes: dict[str, int] = {}
        for key, item in value.items():
            out[_scan_key(key, value, out, suffixes, source=source)] = _scan_structured(
                item, source, depth + 1, budget
            )
        return out
    scanned = [_scan_structured(item, source, depth + 1, budget) for item in value]
    return tuple(scanned) if isinstance(value, tuple) else scanned


def scan_structured_content(value: Any, *, source: str) -> Any:
    """Scan every string in an MCP ``structuredContent`` payload.

    A tool declaring an ``outputSchema`` returns its typed payload as
    ``structuredContent``, and :meth:`OperationsMixin.call_tool` hands that
    object to the caller in preference to the text mirror. Only the text parts
    used to be scanned, so a server could put an injection in a JSON field and
    skip the boundary entirely. Every string leaf — and every object key — is
    now passed through :func:`~core.guardrails.scan_external_content` with the
    same policy as text parts: findings are always logged, and flagged strings
    are sanitized only under ``BASELITH_SANITIZE_EXTERNAL_CONTENT``. Numbers,
    booleans and ``null`` are returned untouched, the shape is preserved, and
    the input is never mutated.

    The recursive walk is bounded by :data:`STRUCTURED_SCAN_MAX_DEPTH` and
    :data:`STRUCTURED_SCAN_MAX_NODES`; a subtree past either bound is scanned
    for detection as one block of its joined strings and, only when flagged
    under the sanitize policy, copied with its strings sanitized through an
    explicit stack — container types never change at any depth. A sanitized
    key that would collide with another key keeps its original text (logged)
    so no value is ever dropped.

    Args:
        value: The ``structuredContent`` value (normally a JSON object).
        source: Origin label recorded with any finding.

    Returns:
        A scanned copy of ``value``.
    """
    return _scan_structured(value, source, 0, [STRUCTURED_SCAN_MAX_NODES])


class OperationsMixin:
    """Tool and resource calls over whichever transport is connected."""

    input_provider: Any
    cache: Any
    # Supplied by MCPClient.
    _http: Any
    _ensure_connected: Callable[[], None]
    _send_request: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]

    # -------------------------------------------------------------------------
    # Tool Operations
    # -------------------------------------------------------------------------

    async def list_tools(self) -> list[MCPToolInfo]:
        """
        List available tools from the server.

        Returns:
            List of tool information
        """
        self._ensure_connected()

        response = await self._send_request("tools/list", {})
        tools = response.get("tools", [])

        if self._http is not None:
            # The HTTP transport needs the schemas to mirror `x-mcp-header`
            # parameters into headers on the next tools/call.
            self._http.tool_schemas = {
                t["name"]: t.get("inputSchema", {}) for t in tools
            }

        return [
            MCPToolInfo(
                name=t["name"],
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
            )
            for t in tools
        ]

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> Any:
        """
        Call a tool on the server.

        Args:
            name: Tool name
            arguments: Tool arguments

        Returns:
            Tool result
        """
        self._ensure_connected()

        response = await self._round_trip(
            "tools/call", {"name": name, "arguments": arguments or {}}
        )

        # A tool that reported `isError: true` executed and failed: surfacing
        # its content as a normal result would hand the model a failure message
        # dressed as data.
        if response.get("isError"):
            raise MCPToolError(error_text(response) or f"Tool '{name}' failed")

        # A tool declaring an outputSchema returns the typed payload directly;
        # prefer it over re-parsing the text mirror sent for older clients.
        # It is as untrusted as the text parts: every string in it is scanned.
        if "structuredContent" in response:
            return scan_structured_content(
                response["structuredContent"], source=f"mcp_tool:{name}"
            )

        # Extract content from response
        content = response.get("content", [])
        if not content:
            return None

        # External MCP servers are untrusted: every text part is scanned for
        # indirect prompt injection before it enters the agent's context
        # (sanitized under BASELITH_SANITIZE_EXTERNAL_CONTENT, on by default).
        content = scan_content_parts(content, source=f"mcp_tool:{name}")

        # Return text content if single item
        if len(content) == 1 and content[0].get("type") == "text":
            text = content[0].get("text", "")
            # Try to parse as JSON
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text

        return content

    async def _round_trip(
        self, method: str, params: dict[str, Any], max_rounds: int = 4
    ) -> dict[str, Any]:
        """Send *method*, fulfilling any input the server asks for, and return
        the final result.

        An ``InputRequiredResult`` is not a failure: the server is asking for
        elicitation, sampling or roots (MRTR, 2026-07-28). Each retry is an
        independent request with a fresh id, echoing ``requestState`` verbatim —
        the value is opaque and must never be inspected or altered.

        Raises:
            MCPToolError: The server kept asking after *max_rounds*, or asked
                while this client declared no way to answer.
        """
        payload = dict(params)
        for _ in range(max_rounds):
            response = await self._send_request(method, payload)
            if response.get("resultType") != "input_required":
                return response

            requests = response.get("inputRequests") or {}
            if requests and self.input_provider is None:
                raise MCPToolError(
                    f"Server asked for input on '{method}' but this client has "
                    "no input_provider configured"
                )

            payload = dict(params)
            if requests:
                payload["inputResponses"] = await self.input_provider(requests)
            if "requestState" in response:
                payload["requestState"] = response["requestState"]

        raise MCPToolError(
            f"Server still requesting input for '{method}' after {max_rounds} rounds"
        )

    # -------------------------------------------------------------------------
    # Resource Operations
    # -------------------------------------------------------------------------

    async def list_resources(self) -> list[dict[str, Any]]:
        """List available resources from the server."""
        self._ensure_connected()

        response = await self._send_request("resources/list", {})
        resources: list[dict[str, Any]] = response.get("resources", [])
        return resources

    async def read_resource(self, uri: str) -> Any:
        """Read a resource from the server."""
        self._ensure_connected()

        response = await self._send_request("resources/read", {"uri": uri})
        contents = scan_content_parts(
            response.get("contents", []), source=f"mcp_resource:{uri}"
        )

        if contents and len(contents) == 1:
            return contents[0].get("text")

        return contents
