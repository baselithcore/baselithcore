"""Standard request headers for the modern Streamable HTTP transport.

Revision 2026-07-28 mirrors selected body fields into HTTP headers so gateways
and load balancers can route without parsing the body. That only stays safe if
the two agree: a proxy authorizing on ``Mcp-Name`` while the server executes
the body value is exactly the confused-deputy split the spec closes by
requiring the server to reject any mismatch with ``HeaderMismatch``.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

from core.mcp.errors import HeaderMismatch
from core.mcp.modern import PROTOCOL_VERSION_KEY

PROTOCOL_VERSION_HEADER = "MCP-Protocol-Version"
METHOD_HEADER = "Mcp-Method"
NAME_HEADER = "Mcp-Name"

_SENTINEL_PREFIX = "=?base64?"
_SENTINEL_SUFFIX = "?="

# Methods whose primary subject is mirrored into `Mcp-Name`, and the params
# field it comes from.
NAME_SOURCE_FIELDS = {
    "tools/call": "name",
    "resources/read": "uri",
    "prompts/get": "name",
}


def decode_header_value(value: str) -> str:
    """Decode the ``=?base64?…?=`` sentinel form, else return *value* as-is.

    Raises:
        HeaderMismatch: The value claims to be Base64 but is not decodable.
    """
    if not (value.startswith(_SENTINEL_PREFIX) and value.endswith(_SENTINEL_SUFFIX)):
        return value
    payload = value[len(_SENTINEL_PREFIX) : -len(_SENTINEL_SUFFIX)]
    try:
        return base64.b64decode(payload, validate=True).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise HeaderMismatch(f"Malformed Base64 header value: {value}") from exc


def encode_header_value(value: str) -> str:
    """Encode *value* for an HTTP header, using the sentinel when needed.

    Values outside printable ASCII, or with edge whitespace, cannot travel
    literally; a plain value that happens to look like the sentinel is encoded
    too, so it can never be mistaken for one.
    """
    literal_safe = (
        value.isascii()
        and value.strip() == value
        and all(0x20 <= ord(ch) <= 0x7E for ch in value)
        and not (
            value.startswith(_SENTINEL_PREFIX) and value.endswith(_SENTINEL_SUFFIX)
        )
    )
    if literal_safe:
        return value
    encoded = base64.b64encode(value.encode()).decode()
    return f"{_SENTINEL_PREFIX}{encoded}{_SENTINEL_SUFFIX}"


def standard_headers(message: dict[str, Any]) -> dict[str, str]:
    """The standard headers a modern request must carry, derived from *message*.

    Returns an empty mapping for a legacy request (no per-request ``_meta``),
    since those headers are only defined from 2026-07-28 onwards.
    """
    params = message.get("params") or {}
    version = (params.get("_meta") or {}).get(PROTOCOL_VERSION_KEY)
    if version is None:
        return {}

    method = message.get("method", "")
    headers = {PROTOCOL_VERSION_HEADER: version, METHOD_HEADER: method}

    source_field = NAME_SOURCE_FIELDS.get(method)
    if source_field is not None and params.get(source_field) is not None:
        headers[NAME_HEADER] = encode_header_value(str(params[source_field]))
    return headers


def validate_modern_headers(headers: Any, message: dict[str, Any]) -> None:
    """Check the standard headers against the request body.

    Args:
        headers: Case-insensitive mapping of the request's HTTP headers.
        message: The parsed JSON-RPC request body.

    Raises:
        HeaderMismatch: A required header is missing or disagrees with the body.
    """
    method = message.get("method", "")
    params = message.get("params") or {}

    declared_version = headers.get(PROTOCOL_VERSION_HEADER)
    if declared_version is None:
        raise HeaderMismatch(f"Missing required header: {PROTOCOL_VERSION_HEADER}")
    body_version = params.get("_meta", {}).get(PROTOCOL_VERSION_KEY)
    if declared_version != body_version:
        raise HeaderMismatch(
            f"Header mismatch: {PROTOCOL_VERSION_HEADER} header value "
            f"'{declared_version}' does not match body value '{body_version}'"
        )

    declared_method = headers.get(METHOD_HEADER)
    if declared_method is None:
        raise HeaderMismatch(f"Missing required header: {METHOD_HEADER}")
    if declared_method != method:
        raise HeaderMismatch(
            f"Header mismatch: {METHOD_HEADER} header value '{declared_method}' "
            f"does not match body value '{method}'"
        )

    source_field = NAME_SOURCE_FIELDS.get(method)
    if source_field is None:
        return

    declared_name = headers.get(NAME_HEADER)
    if declared_name is None:
        raise HeaderMismatch(f"Missing required header: {NAME_HEADER}")
    body_name = params.get(source_field)
    if decode_header_value(declared_name) != body_name:
        raise HeaderMismatch(
            f"Header mismatch: {NAME_HEADER} header value '{declared_name}' "
            f"does not match body value '{body_name}'"
        )


__all__ = [
    "METHOD_HEADER",
    "encode_header_value",
    "standard_headers",
    "NAME_HEADER",
    "NAME_SOURCE_FIELDS",
    "PROTOCOL_VERSION_HEADER",
    "decode_header_value",
    "validate_modern_headers",
]
