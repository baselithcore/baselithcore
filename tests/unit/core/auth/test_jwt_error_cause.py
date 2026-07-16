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

import jwt as pyjwt
import pytest

from core.auth.jwt import JWTHandler
from core.auth.types import AuthRole

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
