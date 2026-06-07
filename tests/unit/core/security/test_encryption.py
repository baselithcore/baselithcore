"""Unit tests for the AES-256-GCM field encryptor."""

import base64

import pytest
from pydantic import SecretStr

from core.security.encryption import (
    DecryptionError,
    EncryptionError,
    FieldEncryptor,
)


def _enc(secret: str = "a-sufficiently-long-passphrase-for-testing") -> FieldEncryptor:
    return FieldEncryptor.from_keys({"v1": SecretStr(secret)})


class TestRoundTrip:
    def test_encrypt_decrypt_roundtrip(self):
        enc = _enc()
        token = enc.encrypt("super-secret-value")
        assert token != "super-secret-value"
        assert enc.decrypt(token) == "super-secret-value"

    def test_token_is_self_describing(self):
        enc = _enc()
        token = enc.encrypt("x")
        assert token.startswith("enc:v1:v1:")

    def test_empty_string_roundtrip(self):
        enc = _enc()
        assert enc.decrypt(enc.encrypt("")) == ""

    def test_unicode_roundtrip(self):
        enc = _enc()
        value = "héllo — 日本語 🔐"
        assert enc.decrypt(enc.encrypt(value)) == value

    def test_nonce_is_random_per_encryption(self):
        enc = _enc()
        assert enc.encrypt("same") != enc.encrypt("same")

    def test_bytes_roundtrip_with_aad(self):
        enc = _enc()
        token = enc.encrypt_bytes(b"\x00\x01\x02", aad=b"tenant-42")
        assert enc.decrypt_bytes(token, aad=b"tenant-42") == b"\x00\x01\x02"

    def test_wrong_aad_fails(self):
        enc = _enc()
        token = enc.encrypt_bytes(b"data", aad=b"tenant-42")
        with pytest.raises(DecryptionError):
            enc.decrypt_bytes(token, aad=b"tenant-99")


class TestIdempotenceAndPassthrough:
    def test_encrypt_is_idempotent(self):
        enc = _enc()
        once = enc.encrypt("v")
        twice = enc.encrypt(once)
        assert once == twice

    def test_decrypt_passes_through_plaintext(self):
        enc = _enc()
        assert enc.decrypt("not-a-token") == "not-a-token"

    def test_is_encrypted(self):
        enc = _enc()
        assert FieldEncryptor.is_encrypted(enc.encrypt("v"))
        assert not FieldEncryptor.is_encrypted("plain")


class TestTamperDetection:
    def test_tampered_ciphertext_rejected(self):
        enc = _enc()
        token = enc.encrypt("value")
        prefix, version, key_id, blob = token.split(":", 3)
        raw = bytearray(base64.urlsafe_b64decode(blob + "=" * (-len(blob) % 4)))
        raw[-1] ^= 0x01  # flip a bit in the auth tag
        bad = base64.urlsafe_b64encode(bytes(raw)).decode()
        with pytest.raises(DecryptionError):
            enc.decrypt(f"{prefix}:{version}:{key_id}:{bad}")

    def test_unknown_key_id_rejected(self):
        enc = _enc()
        token = enc.encrypt("value")
        _, version, _, blob = token.split(":", 3)
        with pytest.raises(DecryptionError, match="Unknown encryption key id"):
            enc.decrypt(f"enc:{version}:ghost:{blob}")

    def test_malformed_token_rejected(self):
        enc = _enc()
        with pytest.raises(DecryptionError):
            enc.decrypt_bytes("enc:v1:onlythree")

    def test_unsupported_version_rejected(self):
        enc = _enc()
        token = enc.encrypt("value")
        _, _, key_id, blob = token.split(":", 3)
        with pytest.raises(DecryptionError, match="Unsupported token scheme"):
            enc.decrypt(f"enc:v99:{key_id}:{blob}")


class TestKeyRotation:
    def test_decrypt_with_old_key_after_rotation(self):
        old = FieldEncryptor.from_keys({"v1": SecretStr("old-passphrase-xxxxxxxxxx")})
        old_token = old.encrypt("secret")

        rotated = FieldEncryptor.from_keys(
            {
                "v1": SecretStr("old-passphrase-xxxxxxxxxx"),
                "v2": SecretStr("new-passphrase-yyyyyyyyyy"),
            },
            active_key_id="v2",
        )
        # Old ciphertext still decrypts...
        assert rotated.decrypt(old_token) == "secret"
        # ...and is flagged for rotation.
        assert rotated.needs_rotation(old_token)
        # New encryption uses the active key.
        new_token = rotated.encrypt("secret")
        assert new_token.startswith("enc:v1:v2:")
        assert not rotated.needs_rotation(new_token)

    def test_active_key_required_when_multiple(self):
        with pytest.raises(EncryptionError, match="active_key_id is required"):
            FieldEncryptor.from_keys(
                {
                    "v1": SecretStr("aaaaaaaaaaaaaaaaaaaa"),
                    "v2": SecretStr("bbbbbbbbbbbbbbbbbbbb"),
                }
            )


class TestKeyDerivation:
    def test_raw_base64_key_used_directly(self):
        raw = base64.urlsafe_b64encode(b"\x11" * 32).decode()
        e1 = FieldEncryptor.from_keys({"k": SecretStr(raw)})
        e2 = FieldEncryptor.from_keys({"k": SecretStr(raw)})
        # Same raw key -> e2 can decrypt e1's output.
        assert e2.decrypt(e1.encrypt("interop")) == "interop"

    def test_construction_requires_a_key(self):
        with pytest.raises(EncryptionError):
            FieldEncryptor.from_keys({})

    def test_key_id_may_not_contain_colon(self):
        with pytest.raises(EncryptionError, match="must not contain"):
            FieldEncryptor.from_keys({"bad:id": SecretStr("xxxxxxxxxxxxxxxxxxxx")})
