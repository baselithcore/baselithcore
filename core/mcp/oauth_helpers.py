"""Small pure helpers of the OAuth client: PKCE, origin comparison, well-known URLs."""

from __future__ import annotations

import base64
import hashlib
import secrets
from typing import Any
from urllib.parse import urlparse

from pydantic import SecretStr

from core.mcp.oauth_types import PKCEPair


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_pkce() -> PKCEPair:
    """A fresh PKCE verifier (86 URL-safe characters) and its S256 challenge."""
    verifier = secrets.token_urlsafe(64)
    challenge = b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return PKCEPair(verifier=SecretStr(verifier), challenge=challenge)


def secret(value: str | SecretStr) -> str:
    return value.get_secret_value() if isinstance(value, SecretStr) else value


def strs(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def origin(url: str) -> tuple[str, str, int | None]:
    """``(scheme, lowercase host without a trailing dot, port)`` of *url*, default ports folded."""
    parsed = urlparse(url)
    try:
        port = parsed.port
    except ValueError:
        port = -1
    scheme = parsed.scheme.lower()
    default = {"https": 443, "http": 80}.get(scheme)
    return (
        scheme,
        (parsed.hostname or "").rstrip("."),
        default if port is None else port,
    )


def well_known(base: str, suffix: str) -> str:
    """Insert *suffix* between host and path (RFC 8414 3.1 / RFC 9728 3.1)."""
    parsed = urlparse(base)
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{suffix}{path}"


__all__ = ["b64url", "generate_pkce", "origin", "secret", "strs", "well_known"]
