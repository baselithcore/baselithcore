"""``verify_token`` preserves the underlying PyJWT cause subtype.

The auth audit log collapses a failed bearer verification to the exception
*class* name, but :meth:`JWTHandler.verify_token` wraps every non-expiry PyJWT
failure into the base ``InvalidTokenError`` — making that name opaque
("InvalidTokenError" for bad-signature, malformed, wrong-audience, …). The wrap
uses ``raise ... from e``, so ``__cause__`` keeps the real PyJWT subtype, which
``AuthManager`` now surfaces in the warning. These tests pin that chain so the
diagnostic detail cannot silently regress.
"""

from __future__ import annotations

from unittest.mock import patch

import jwt as pyjwt
import pytest

from core.auth.jwt import JWTHandler
from core.auth.types import AuthRole
from core.utils.logsafe import sanitize_log_value

_SECRET = "test-secret-with-at-least-thirty-two-chars"
_OTHER_SECRET = "another-secret-also-at-least-thirty-two!"


@pytest.mark.asyncio
async def test_bad_signature_cause_is_invalid_signature() -> None:
    signer = JWTHandler(secret_key=_SECRET)
    verifier = JWTHandler(secret_key=_OTHER_SECRET)
    token = signer.create_token("u1", roles={AuthRole.USER})

    with pytest.raises(Exception) as exc_info:
        await verifier.verify_token(token)

    # Manager derives detail from type(__cause__).__name__ → "InvalidSignatureError".
    assert isinstance(exc_info.value.__cause__, pyjwt.InvalidSignatureError)


@pytest.mark.asyncio
async def test_malformed_token_cause_is_decode_error() -> None:
    verifier = JWTHandler(secret_key=_SECRET)

    with pytest.raises(Exception) as exc_info:
        await verifier.verify_token("this-is-not-a-jwt")

    # A non-JWT credential sent as a bearer (e.g. a PAT) lands here.
    assert isinstance(exc_info.value.__cause__, pyjwt.DecodeError)


@pytest.mark.asyncio
async def test_message_is_generic_while_the_reason_is_logged() -> None:
    """The raised message reaches the client as the 401 ``detail``.

    It must therefore not name the failing check — PyJWT's "Signature
    verification failed" tells a forger exactly what to fix next. The reason
    goes to the log instead, alongside the PyJWT class name.
    """
    signer = JWTHandler(secret_key=_SECRET)
    verifier = JWTHandler(secret_key=_OTHER_SECRET)
    token = signer.create_token("u1", roles={AuthRole.USER})

    with patch("core.auth.jwt.logger") as mock_logger:
        with pytest.raises(Exception) as exc_info:
            await verifier.verify_token(token)

    assert str(exc_info.value) == "Invalid token"
    # Nothing about signatures, audiences or issuers escapes to the caller.
    assert "ignature" not in str(exc_info.value)

    mock_logger.warning.assert_called_once()
    args, kwargs = mock_logger.warning.call_args
    assert args[0] == "jwt_verification_failed"
    assert kwargs["reason"] == "InvalidSignatureError"
    assert "Signature verification failed" in kwargs["detail"]


@pytest.mark.asyncio
async def test_logged_reason_is_sanitized_and_caller_input_stays_out_of_the_body() -> (
    None
):
    """An unknown ``kid`` is echoed by the failing decode into its message.

    The logged reason therefore carries caller-controlled text and goes through
    ``sanitize_log_value`` (no raw newline can forge a second audit record),
    while the response body stays the fixed generic string.
    """
    verifier = JWTHandler(secret_key=_SECRET, keys={"k1": _SECRET}, active_kid="k1")
    forged_kid = "x'\nAUDIT | AUTH | ok | user=admin"
    token = pyjwt.encode(
        {"sub": "u1", "exp": 9999999999},
        _SECRET,
        algorithm="HS256",
        headers={"kid": forged_kid},
    )

    with patch("core.auth.jwt.logger") as mock_logger:
        with pytest.raises(Exception) as exc_info:
            await verifier.verify_token(token)

    # The forged kid never reaches the caller.
    assert str(exc_info.value) == "Invalid token"
    assert "AUDIT" not in str(exc_info.value)

    detail = mock_logger.warning.call_args.kwargs["detail"]
    # Exactly the sanitizer's output for the underlying message: single line,
    # printable only, length-capped.
    assert detail == sanitize_log_value(str(exc_info.value.__cause__))
    assert "\n" not in detail
    assert "Unknown JWT key id" in detail


@pytest.mark.asyncio
async def test_sanitizer_escapes_a_raw_newline_in_the_reason() -> None:
    """The sanitizer is what stands between a control character in the decode
    error and a forged log record; pin it on the exact call site."""
    verifier = JWTHandler(secret_key=_SECRET)
    raw = pyjwt.InvalidTokenError("line one\nAUDIT | AUTH | ok | user=admin")

    with patch.object(verifier._keyring, "decode", side_effect=raw):
        with patch("core.auth.jwt.logger") as mock_logger:
            with pytest.raises(Exception):
                await verifier.verify_token("whatever")

    detail = mock_logger.warning.call_args.kwargs["detail"]
    assert "\n" not in detail
    assert "\\x0a" in detail
