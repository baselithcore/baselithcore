"""RFC 8414 metadata and JWKS document construction."""

from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from core.auth.oauth import GrantType, build_jwks_document, build_metadata_document


def test_metadata_advertises_issuer_and_endpoints() -> None:
    doc = build_metadata_document(
        issuer="https://baselith.example",
        grant_types=[GrantType.AUTHORIZATION_CODE, GrantType.DEVICE_CODE],
        scopes=["chat:read", "chat:write"],
    )
    assert doc["issuer"] == "https://baselith.example"
    assert doc["authorization_endpoint"] == (
        "https://baselith.example/api/auth/oauth/authorize"
    )
    assert doc["token_endpoint"] == "https://baselith.example/api/auth/oauth/token"
    assert doc["jwks_uri"] == "https://baselith.example/.well-known/jwks.json"


def test_metadata_declares_s256_only() -> None:
    doc = build_metadata_document(
        issuer="https://baselith.example",
        grant_types=[GrantType.AUTHORIZATION_CODE],
        scopes=["chat:read"],
    )
    assert doc["code_challenge_methods_supported"] == ["S256"]
    assert "plain" not in doc["code_challenge_methods_supported"]


def test_metadata_never_advertises_implicit_or_password() -> None:
    doc = build_metadata_document(
        issuer="https://baselith.example",
        grant_types=list(GrantType),
        scopes=["chat:read"],
    )
    assert "token" not in doc["response_types_supported"]
    assert "password" not in doc["grant_types_supported"]


def test_jwks_document_exposes_public_keys_by_kid() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    pem = (
        key.public_key()
        .public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    doc = build_jwks_document({"key-2026-08": pem})
    (jwk,) = doc["keys"]
    assert jwk["kid"] == "key-2026-08"
    assert jwk["kty"] == "EC"
    assert jwk["alg"] == "ES256"
    assert jwk["use"] == "sig"
    assert "d" not in jwk  # never leak the private scalar
