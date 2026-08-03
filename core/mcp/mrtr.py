"""Multi Round-Trip Requests (SEP-2322).

2026-07-28 removed server-initiated requests. When a handler needs elicitation,
sampling or a roots listing, it raises :class:`InputRequired`; the dispatcher
turns that into an ``InputRequiredResult`` and the client retries the *original*
request carrying the answers. The retry is an independent request, so anything
the server must remember travels in ``requestState`` — through the client, and
therefore under an attacker's control.

:class:`RequestStateSealer` is why that is safe: the blob is HMAC-sealed and
bound to the authenticated principal, the originating method and a short expiry,
so it cannot be forged, replayed by another user, or moved to another request.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from contextvars import ContextVar
from typing import Any

from core.observability.logging import get_logger

logger = get_logger(__name__)

# Client capability required to issue each kind of input request.
CAPABILITY_FOR_METHOD = {
    "elicitation/create": "elicitation",
    "sampling/createMessage": "sampling",
    "roots/list": "roots",
}

# Answers and server state carried by the request currently being served.
input_responses: ContextVar[dict[str, Any] | None] = ContextVar(
    "mcp_input_responses", default=None
)
request_state: ContextVar[Any] = ContextVar("mcp_request_state", default=None)


def get_input_responses() -> dict[str, Any]:
    """The ``inputResponses`` the client sent on this retry (empty on first try)."""
    return input_responses.get() or {}


def get_request_state() -> Any:
    """The verified state this server sealed into the previous response."""
    return request_state.get()


class InputRequired(Exception):
    """Raised by a handler that needs the client to supply something first.

    Args:
        requests: ``InputRequests`` map — server-assigned key → request object
            (``elicitation/create``, ``sampling/createMessage``, ``roots/list``).
        state: Arbitrary JSON-serializable context to seal into ``requestState``
            and get back verbatim on the retry.
    """

    def __init__(
        self, requests: dict[str, Any] | None = None, state: Any = None
    ) -> None:
        if not requests and state is None:
            raise ValueError("InputRequired needs inputRequests or state (or both)")
        super().__init__("input required")
        self.requests = requests or {}
        self.state = state

    def required_capabilities(self) -> list[str]:
        """Client capabilities the requests depend on, deduplicated."""
        seen: list[str] = []
        for request in self.requests.values():
            capability = CAPABILITY_FOR_METHOD.get(request.get("method", ""))
            if capability and capability not in seen:
                seen.append(capability)
        return seen


class RequestStateSealer:
    """Seals opaque server state for a round trip through the client."""

    def __init__(self, secret: bytes | None = None, ttl_seconds: int = 300) -> None:
        """
        Args:
            secret: HMAC key. A random per-process key is generated when absent,
                which is correct for a single instance and means a multi-replica
                deployment must configure a shared secret instead.
            ttl_seconds: How long a sealed state stays valid.
        """
        self._secret = secret or secrets.token_bytes(32)
        self._ttl = ttl_seconds

    def seal(self, state: Any, principal: str | None, request: str) -> str:
        """Return the sealed, URL-safe token for *state*."""
        payload = {
            "s": state,
            "p": principal,
            "r": request,
            "e": time.time() + self._ttl,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        signature = hmac.new(self._secret, raw, hashlib.sha256).digest()
        return (
            base64.urlsafe_b64encode(raw).decode().rstrip("=")
            + "."
            + base64.urlsafe_b64encode(signature).decode().rstrip("=")
        )

    def unseal(self, token: str, principal: str | None, request: str) -> Any:
        """Verify *token* and return the sealed state.

        Raises:
            ValueError: The token is malformed, forged, expired, or was issued
                for a different principal or request.
        """
        try:
            raw_part, signature_part = token.split(".", 1)
            raw = _b64decode(raw_part)
            signature = _b64decode(signature_part)
        except (ValueError, TypeError) as exc:
            raise ValueError("Malformed requestState") from exc

        expected = hmac.new(self._secret, raw, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("requestState failed integrity verification")

        payload = json.loads(raw.decode())
        if payload.get("e", 0) <= time.time():
            raise ValueError("requestState has expired")
        if payload.get("p") != principal:
            raise ValueError("requestState was issued for a different principal")
        if payload.get("r") != request:
            raise ValueError("requestState was issued for a different request")
        return payload.get("s")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def input_required_result(
    exc: InputRequired, sealed_state: str | None
) -> dict[str, Any]:
    """Build the ``InputRequiredResult`` for *exc*."""
    result: dict[str, Any] = {"resultType": "input_required"}
    if exc.requests:
        result["inputRequests"] = exc.requests
    if sealed_state is not None:
        result["requestState"] = sealed_state
    return result


class LegacyInputUnsupported(Exception):
    """A legacy client asked for something only MRTR can deliver."""


class RoundTripMixin:
    """Shared MRTR plumbing for the handlers that may ask for input."""

    _state_sealer: Any

    @staticmethod
    def _principal() -> str | None:
        """The authenticated identity a sealed state is bound to."""
        from core.context import get_current_user_id

        return get_current_user_id()

    def _enter_round_trip(self, params: dict[str, Any], method: str) -> Any:
        """Bind this request's answers and verified state for the handler.

        Raises:
            InvalidParams: ``requestState`` is forged, expired, or was issued
                for a different principal or request — all of which mean the
                client is not the one this state was minted for.
        """
        from core.mcp.errors import InvalidParams

        state: Any = None
        sealed = params.get("requestState")
        if sealed is not None:
            try:
                state = self._state_sealer.unseal(
                    str(sealed), principal=self._principal(), request=method
                )
            except ValueError as exc:
                logger.warning("mcp_request_state_rejected", error=str(exc))
                raise InvalidParams(f"Invalid requestState: {exc}") from exc

        answers = params.get("inputResponses")
        return (
            input_responses.set(answers if isinstance(answers, dict) else {}),
            request_state.set(state),
        )

    @staticmethod
    def _exit_round_trip(tokens: Any) -> None:
        answers_token, state_token = tokens
        input_responses.reset(answers_token)
        request_state.reset(state_token)

    def _input_required(self, exc: InputRequired, method: str) -> dict[str, Any]:
        """Turn an :class:`InputRequired` into the result the client expects.

        Raises:
            MissingRequiredClientCapability: The handler asked for something
                the client never declared it can provide — the spec forbids
                sending such a request rather than letting it fail downstream.
        """
        from core.mcp.errors import MissingRequiredClientCapability
        from core.mcp.modern import request_meta

        meta = request_meta.get()
        if meta is None:
            # MRTR does not exist before 2026-07-28. Report it as a tool error
            # instead of emitting a result the client cannot parse.
            raise LegacyInputUnsupported(
                "This operation needs client input, which requires protocol "
                "revision 2026-07-28 or newer."
            )

        missing = [c for c in exc.required_capabilities() if not meta.supports(c)]
        if missing:
            raise MissingRequiredClientCapability(
                f"Client must support: {', '.join(missing)}",
                data={"requiredCapabilities": missing},
            )

        sealed = (
            self._state_sealer.seal(
                exc.state, principal=self._principal(), request=method
            )
            if exc.state is not None
            else None
        )
        return input_required_result(exc, sealed)


__all__ = [
    "CAPABILITY_FOR_METHOD",
    "InputRequired",
    "LegacyInputUnsupported",
    "RoundTripMixin",
    "RequestStateSealer",
    "get_input_responses",
    "get_request_state",
    "input_required_result",
    "input_responses",
    "request_state",
]
