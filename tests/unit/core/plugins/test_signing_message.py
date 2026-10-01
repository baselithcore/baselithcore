"""Generic Ed25519 message primitives behind the plugin-hash helpers."""

from __future__ import annotations


def test_sign_message_round_trip() -> None:
    from core.plugins.signing import generate_keypair_hex, sign_message, verify_message

    priv, pub = generate_keypair_hex()
    sig = sign_message(b"hello", priv)
    assert verify_message(b"hello", sig, [pub])
    assert not verify_message(b"hellO", sig, [pub])
    assert not verify_message(b"hello", "zz", [pub])
    assert not verify_message(b"hello", sig, [])


def test_plugin_hash_helpers_delegate_unchanged() -> None:
    from core.plugins.signing import (
        generate_keypair_hex,
        sign_message,
        sign_plugin_hash,
        verify_plugin_signature,
    )

    priv, pub = generate_keypair_hex()
    digest = "AB" * 32
    assert sign_plugin_hash(digest, priv) == sign_message(
        digest.lower().encode("ascii"), priv
    )
    assert verify_plugin_signature(digest, sign_plugin_hash(digest, priv), [pub])
