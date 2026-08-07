"""Key rotation must not log everybody out.

With a single shared secret there is exactly one accepted key, so changing it
invalidates every live session at once — which in practice means it is never
changed. A key ring accepts several keys while signing with one, making the
rotation a sequence of individually safe steps.
"""

from unittest.mock import AsyncMock, patch

import jwt as pyjwt
import pytest

from core.auth._jwt_keys import JWTKeyRing, parse_key_map

SECRET_A = "key-alpha-with-at-least-thirty-two-characters"
SECRET_B = "key-bravo-with-at-least-thirty-two-characters"


@pytest.fixture
def handler_factory():
    from core.auth.jwt import JWTHandler

    def _make(**kwargs):
        with patch("core.auth.jwt.create_redis_client") as redis_factory:
            # `get` must answer None, not a truthy Mock — otherwise every token
            # reads as blacklisted and the signature path is never reached.
            redis_factory.return_value = AsyncMock(get=AsyncMock(return_value=None))
            return JWTHandler(secret_key=SECRET_A, **kwargs)

    return _make


# --------------------------------------------------------------------------- #
# Key map parsing
# --------------------------------------------------------------------------- #


def test_parse_key_map_reads_pairs():
    assert parse_key_map("k1=aaa,k2=bbb") == {"k1": "aaa", "k2": "bbb"}


def test_parse_key_map_restores_escaped_newlines():
    """PEM material survives an env var that cannot hold real newlines."""
    assert parse_key_map("k1=-----BEGIN----\\nBODY") == {"k1": "-----BEGIN----\nBODY"}


def test_parse_key_map_skips_malformed_entries_without_aborting():
    """One typo must not take down startup for a whole multi-key ring."""
    assert parse_key_map("k1=aaa,garbage,=nokey,k2=bbb") == {"k1": "aaa", "k2": "bbb"}


def test_parse_key_map_of_nothing_is_empty():
    assert parse_key_map(None) == {}
    assert parse_key_map("") == {}


# --------------------------------------------------------------------------- #
# Ring construction
# --------------------------------------------------------------------------- #


def test_single_key_needs_no_explicit_active_kid():
    ring = JWTKeyRing(secret_key=SECRET_A, keys={"k1": SECRET_A})
    assert ring.active_kid == "k1"


def test_several_keys_require_an_explicit_choice():
    """Otherwise a rotation would silently keep signing with an arbitrary key."""
    with pytest.raises(ValueError, match="JWT_ACTIVE_KID"):
        JWTKeyRing(secret_key=SECRET_A, keys={"k1": SECRET_A, "k2": SECRET_B})


def test_active_kid_must_exist_in_the_ring():
    with pytest.raises(ValueError, match="not present"):
        JWTKeyRing(secret_key=SECRET_A, keys={"k1": SECRET_A}, active_kid="k9")


def test_forbidden_algorithms_are_refused():
    for bad in ("none", "None", "NONE", ""):
        with pytest.raises(ValueError, match="not allowed"):
            JWTKeyRing(secret_key=SECRET_A, algorithm=bad)


def test_asymmetric_without_a_private_key_refuses_to_sign():
    """A verify-only service must fail loudly, not mint unverifiable tokens."""
    ring = JWTKeyRing(secret_key=SECRET_A, algorithm="RS256")
    with pytest.raises(RuntimeError, match="JWT_SIGNING_KEY"):
        _ = ring.signing_key


# --------------------------------------------------------------------------- #
# End-to-end through JWTHandler
# --------------------------------------------------------------------------- #


def test_no_ring_produces_unlabelled_tokens(handler_factory):
    """Default deployments keep exactly the tokens they had before."""
    handler = handler_factory()
    token = handler.create_token("u-1")
    assert "kid" not in pyjwt.get_unverified_header(token)


def test_active_key_labels_the_token(handler_factory):
    handler = handler_factory(keys={"k1": SECRET_A, "k2": SECRET_B}, active_kid="k2")
    token = handler.create_token("u-1")
    assert pyjwt.get_unverified_header(token)["kid"] == "k2"


async def test_token_signed_by_the_retired_key_still_verifies(handler_factory):
    """The whole point: rotating does not invalidate sessions in flight."""
    old = handler_factory(keys={"k1": SECRET_A}, active_kid="k1")
    token = old.create_token("u-1")

    # Deployment rotates: k2 becomes the signing key, k1 stays accepted.
    rotated = handler_factory(keys={"k1": SECRET_A, "k2": SECRET_B}, active_kid="k2")
    user = await rotated.verify_token(token)
    assert user.user_id == "u-1"


async def test_token_signed_by_the_new_key_verifies(handler_factory):
    rotated = handler_factory(keys={"k1": SECRET_A, "k2": SECRET_B}, active_kid="k2")
    user = await rotated.verify_token(rotated.create_token("u-2"))
    assert user.user_id == "u-2"


async def test_unlabelled_legacy_token_verifies_after_the_ring_arrives(
    handler_factory,
):
    """Tokens minted before the ring existed carry no kid — try every key."""
    legacy = handler_factory()
    token = legacy.create_token("u-3")
    ringed = handler_factory(keys={"k9": SECRET_B}, active_kid="k9")
    user = await ringed.verify_token(token)
    assert user.user_id == "u-3"


async def test_key_dropped_from_the_ring_stops_verifying(handler_factory):
    """Completing a rotation genuinely retires the old key."""
    from core.auth.types import InvalidTokenError

    old = handler_factory(keys={"k1": SECRET_A}, active_kid="k1")
    token = old.create_token("u-4")
    # SECRET_A is gone from both the ring and the deployment secret.
    with patch("core.auth.jwt.create_redis_client") as redis_factory:
        redis_factory.return_value = AsyncMock(get=AsyncMock(return_value=None))
        from core.auth.jwt import JWTHandler

        retired = JWTHandler(
            secret_key=SECRET_B, keys={"k2": SECRET_B}, active_kid="k2"
        )
    with pytest.raises(InvalidTokenError):
        await retired.verify_token(token)


async def test_expired_token_reports_expiry_not_a_key_problem(handler_factory):
    """Trying other keys must not turn an expired token into an invalid one.

    Signed with the *second* ring key and carrying no `kid`, so verification
    walks the candidate list: the first key fails on signature, the second on
    expiry. Expiry is the honest verdict and must survive the loop.
    """
    import time as _time

    from core.auth.types import TokenExpiredError

    handler = handler_factory(keys={"k1": SECRET_A, "k2": SECRET_B}, active_kid="k1")
    expired = pyjwt.encode(
        {"sub": "u-5", "iat": int(_time.time()) - 60, "exp": int(_time.time()) - 10},
        SECRET_B,
        algorithm="HS256",
    )
    with pytest.raises(TokenExpiredError):
        await handler.verify_token(expired)


# --------------------------------------------------------------------------- #
# Asymmetric rings and the HMAC deployment secret
# --------------------------------------------------------------------------- #


def test_asymmetric_ring_never_tries_the_hmac_secret():
    """Under RS*/ES*/EdDSA the deployment secret is not a legitimate
    verification candidate: PyJWT raises InvalidKeyError for it, which is not
    an InvalidTokenError and used to escape callers as a 500."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    private = ed25519.Ed25519PrivateKey.generate()
    public_pem = (
        private.public_key()
        .public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        .decode()
    )
    ring = JWTKeyRing(secret_key=SECRET_A, algorithm="EdDSA", keys={"k1": public_pem})
    assert all(key != SECRET_A for key in ring.candidate_keys())


def test_unverifiable_kidless_token_raises_invalid_token_not_key_error():
    """A kid-less token that no candidate verifies must surface as
    InvalidTokenError (auth failure, 401) — never InvalidKeyError (500)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    private = ed25519.Ed25519PrivateKey.generate()
    public_pem = (
        private.public_key()
        .public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        .decode()
    )
    ring = JWTKeyRing(secret_key=SECRET_A, algorithm="EdDSA", keys={"k1": public_pem})
    # Kid-less HS256 token: wrong algorithm, wrong key — must be a clean reject.
    foreign = pyjwt.encode({"sub": "x"}, SECRET_A, algorithm="HS256")
    with pytest.raises(pyjwt.InvalidTokenError):
        ring.decode(foreign)
