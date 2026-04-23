"""WebSocket node-pairing handshake.

Issues short-lived pairing tokens, validates handshakes, and tracks paired
nodes (macOS menu bar, iOS, Android). Transport is WebSocket; the
``NodePairing`` object is transport-agnostic — connectors are expected to
call ``register_handshake`` with the bytes they received.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

from pydantic import BaseModel, Field

_DEFAULT_TTL_SECONDS = 300.0


class PairingError(RuntimeError):
    """Raised on invalid or expired pairing handshakes."""


class PairingResult(BaseModel):
    node_id: str
    platform: str
    paired_at: float = Field(default_factory=time.time)


class _PendingToken(BaseModel):
    token: str
    expires_at: float
    platform: str | None = None


class NodePairing:
    """Issue + validate pairing tokens; persist paired node identities."""

    def __init__(self, ttl_seconds: float = _DEFAULT_TTL_SECONDS) -> None:
        self._pending: dict[str, _PendingToken] = {}
        self._paired: dict[str, PairingResult] = {}
        self._ttl = ttl_seconds

    def issue_token(self, platform: str | None = None) -> str:
        token = secrets.token_urlsafe(24)
        self._pending[token] = _PendingToken(
            token=token,
            expires_at=time.time() + self._ttl,
            platform=platform,
        )
        return token

    def register_handshake(self, token: str, node_id: str, platform: str) -> PairingResult:
        record = self._pending.pop(token, None)
        if record is None:
            raise PairingError("unknown or already-consumed token")
        if record.expires_at < time.time():
            raise PairingError("token expired")
        if record.platform and record.platform != platform:
            raise PairingError(f"platform mismatch: expected {record.platform}, got {platform}")
        result = PairingResult(node_id=node_id, platform=platform)
        self._paired[node_id] = result
        return result

    def list_paired(self) -> list[PairingResult]:
        return list(self._paired.values())

    def revoke(self, node_id: str) -> bool:
        return self._paired.pop(node_id, None) is not None

    def prune_expired(self) -> int:
        """Drop pending tokens whose expiry has passed."""
        now = time.time()
        expired = [token for token, rec in self._pending.items() if rec.expires_at < now]
        for token in expired:
            self._pending.pop(token, None)
        return len(expired)

    def status(self) -> dict[str, Any]:
        return {
            "paired": len(self._paired),
            "pending_tokens": len(self._pending),
            "ttl_seconds": self._ttl,
        }


__all__ = ["NodePairing", "PairingError", "PairingResult"]
