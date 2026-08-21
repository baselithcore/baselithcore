"""Ed25519 publisher signatures over the plugin integrity hash."""

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.plugins.signing import (
    generate_keypair_hex,
    is_signature_required,
    load_trust_roots,
    sign_plugin_hash,
    verify_plugin_signature,
)

HASH = "a" * 64


def test_sign_and_verify_roundtrip():
    private_hex, public_hex = generate_keypair_hex()
    sig = sign_plugin_hash(HASH, private_hex)
    assert verify_plugin_signature(HASH, sig, [public_hex]) is True


def test_verify_rejects_wrong_key():
    private_hex, _ = generate_keypair_hex()
    _, other_public = generate_keypair_hex()
    sig = sign_plugin_hash(HASH, private_hex)
    assert verify_plugin_signature(HASH, sig, [other_public]) is False


def test_verify_rejects_tampered_hash():
    private_hex, public_hex = generate_keypair_hex()
    sig = sign_plugin_hash(HASH, private_hex)
    assert verify_plugin_signature("b" * 64, sig, [public_hex]) is False


def test_verify_any_of_multiple_roots():
    private_hex, public_hex = generate_keypair_hex()
    _, unrelated = generate_keypair_hex()
    sig = sign_plugin_hash(HASH, private_hex)
    assert verify_plugin_signature(HASH, sig, [unrelated, public_hex]) is True


def test_verify_garbage_signature_is_false_not_raise():
    _, public_hex = generate_keypair_hex()
    assert verify_plugin_signature(HASH, "zz-not-hex", [public_hex]) is False
    assert verify_plugin_signature(HASH, "ab" * 10, [public_hex]) is False


def test_trust_roots_from_env(monkeypatch):
    _, pub1 = generate_keypair_hex()
    _, pub2 = generate_keypair_hex()
    monkeypatch.setenv("BASELITH_PLUGIN_TRUST_ROOTS", f"{pub1}, {pub2}")
    assert load_trust_roots() == [pub1, pub2]
    monkeypatch.delenv("BASELITH_PLUGIN_TRUST_ROOTS")
    assert load_trust_roots() == []


def test_signature_required_flag(monkeypatch):
    monkeypatch.delenv("BASELITH_REQUIRE_PLUGIN_SIGNATURES", raising=False)
    assert is_signature_required() is False
    monkeypatch.setenv("BASELITH_REQUIRE_PLUGIN_SIGNATURES", "true")
    assert is_signature_required() is True


def test_keypair_hex_shapes():
    private_hex, public_hex = generate_keypair_hex()
    assert len(bytes.fromhex(private_hex)) == 32
    assert len(bytes.fromhex(public_hex)) == 32
    # Sanity: the private key really is an Ed25519 seed.
    Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex))
