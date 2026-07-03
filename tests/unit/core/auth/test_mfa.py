"""Tests for TOTP multi-factor authentication (core/auth/mfa.py)."""

from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from pydantic import SecretStr

from core.auth import (
    MFAEnrollment,
    TOTPProvider,
    generate_recovery_codes,
    generate_secret,
    generate_totp,
    hash_recovery_code,
    provisioning_uri,
    verify_recovery_code,
    verify_totp,
)
from core.auth.mfa import DEFAULT_PERIOD


class TestSecretGeneration:
    def test_secret_is_base32_and_unpadded(self):
        secret = generate_secret()
        assert "=" not in secret
        assert secret == secret.upper()
        # base32 alphabet only.
        assert set(secret) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")

    def test_secrets_are_unique(self):
        assert generate_secret() != generate_secret()

    def test_rejects_low_entropy(self):
        with pytest.raises(ValueError):
            generate_secret(num_bytes=8)


class TestTOTPRoundTrip:
    def test_generate_and_verify_now(self):
        secret = generate_secret()
        code = generate_totp(secret)
        assert verify_totp(secret, code) is True

    def test_code_has_expected_shape(self):
        code = generate_totp(generate_secret())
        assert code.isdigit()
        assert len(code) == 6

    def test_wrong_code_rejected(self):
        secret = generate_secret()
        code = generate_totp(secret)
        wrong = "000000" if code != "000000" else "111111"
        assert verify_totp(secret, wrong) is False

    def test_rfc6238_known_vector_sha1(self):
        # RFC 6238 Appendix B test vector: ASCII secret "12345678901234567890"
        # base32-encoded, at T=59s, SHA-1, 8 digits → 94287082.
        import base64

        secret = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
        assert (
            generate_totp(secret, timestamp=59, digits=8, algorithm="sha1")
            == "94287082"
        )

    def test_malformed_codes_rejected(self):
        secret = generate_secret()
        for bad in ["", "abc", "12345", "1234567", "12 34 56", "abcdef"]:
            assert verify_totp(secret, bad) is False

    def test_none_code_is_safe(self):
        secret = generate_secret()
        assert verify_totp(secret, None) is False  # type: ignore[arg-type]


class TestClockSkewWindow:
    def test_previous_step_accepted_within_window(self):
        secret = generate_secret()
        t = 10_000.0
        prev_code = generate_totp(secret, timestamp=t - DEFAULT_PERIOD)
        assert verify_totp(secret, prev_code, timestamp=t, valid_window=1) is True

    def test_next_step_accepted_within_window(self):
        secret = generate_secret()
        t = 10_000.0
        next_code = generate_totp(secret, timestamp=t + DEFAULT_PERIOD)
        assert verify_totp(secret, next_code, timestamp=t, valid_window=1) is True

    def test_outside_window_rejected(self):
        secret = generate_secret()
        t = 10_000.0
        far = generate_totp(secret, timestamp=t + 3 * DEFAULT_PERIOD)
        assert verify_totp(secret, far, timestamp=t, valid_window=1) is False

    def test_zero_window_only_current_step(self):
        secret = generate_secret()
        t = 10_000.0
        prev_code = generate_totp(secret, timestamp=t - DEFAULT_PERIOD)
        assert verify_totp(secret, prev_code, timestamp=t, valid_window=0) is False

    def test_negative_window_raises(self):
        with pytest.raises(ValueError):
            verify_totp(generate_secret(), "123456", valid_window=-1)


class TestProvisioningURI:
    def test_uri_structure(self):
        secret = generate_secret()
        uri = provisioning_uri(secret, "alice@example.com", "BaselithCore")
        parts = urlsplit(uri)
        assert parts.scheme == "otpauth"
        assert parts.netloc == "totp"
        assert "BaselithCore:alice@example.com" in unquote(parts.path)
        params = parse_qs(parts.query)
        assert params["secret"] == [secret]
        assert params["issuer"] == ["BaselithCore"]
        assert params["digits"] == ["6"]
        assert params["period"] == ["30"]
        assert params["algorithm"] == ["SHA1"]


class TestRecoveryCodes:
    def test_generate_count_and_format(self):
        codes = generate_recovery_codes(8)
        assert len(codes) == 8
        for c in codes:
            assert "-" in c
            assert len(c.replace("-", "")) == 16

    def test_codes_are_unique(self):
        codes = generate_recovery_codes(20)
        assert len(set(codes)) == 20

    def test_zero_count_raises(self):
        with pytest.raises(ValueError):
            generate_recovery_codes(0)

    def test_verify_matches_and_returns_hash(self):
        codes = generate_recovery_codes(5)
        hashes = [hash_recovery_code(c) for c in codes]
        matched = verify_recovery_code(codes[2], hashes)
        assert matched == hash_recovery_code(codes[2])

    def test_verify_is_format_insensitive(self):
        codes = generate_recovery_codes(1)
        h = [hash_recovery_code(codes[0])]
        noisy = codes[0].lower().replace("-", " ")
        assert verify_recovery_code(noisy, h) is not None

    def test_unknown_code_returns_none(self):
        hashes = [hash_recovery_code(c) for c in generate_recovery_codes(3)]
        assert verify_recovery_code("AAAAAAAA-BBBBBBBB", hashes) is None

    def test_consumed_hash_can_be_removed(self):
        codes = generate_recovery_codes(3)
        hashes = [hash_recovery_code(c) for c in codes]
        used = verify_recovery_code(codes[0], hashes)
        assert used is not None
        hashes.remove(used)
        # The same code must no longer validate once removed (single-use).
        assert verify_recovery_code(codes[0], hashes) is None


class TestTOTPProvider:
    def test_enroll_produces_consistent_artifacts(self):
        provider = TOTPProvider(issuer="Acme")
        enrollment = provider.enroll("bob@example.com")
        assert isinstance(enrollment, MFAEnrollment)
        assert isinstance(enrollment.secret, SecretStr)
        assert len(enrollment.recovery_codes) == 10
        assert len(enrollment.recovery_code_hashes) == 10
        # Hashes correspond to the plaintext codes.
        for code, h in zip(enrollment.recovery_codes, enrollment.recovery_code_hashes):
            assert hash_recovery_code(code) == h

    def test_enrolled_secret_verifies(self):
        provider = TOTPProvider()
        enrollment = provider.enroll("carol@example.com")
        code = generate_totp(enrollment.secret.get_secret_value())
        assert provider.verify_code(enrollment.secret, code) is True

    def test_provisioning_uri_uses_issuer(self):
        provider = TOTPProvider(issuer="Acme")
        enrollment = provider.enroll("dave@example.com")
        uri = enrollment.provisioning_uri()
        assert "Acme:dave@example.com" in unquote(uri)
        assert "otpauth://totp/" in uri

    def test_verify_code_accepts_secretstr_or_str(self):
        provider = TOTPProvider()
        secret = generate_secret()
        code = generate_totp(secret)
        assert provider.verify_code(secret, code) is True
        assert provider.verify_code(SecretStr(secret), code) is True

    def test_recovery_codes_excluded_from_repr(self):
        enrollment = TOTPProvider().enroll("eve@example.com")
        text = repr(enrollment)
        # SecretStr hides the secret; recovery plaintext is repr=False.
        for code in enrollment.recovery_codes:
            assert code not in text
        assert enrollment.secret.get_secret_value() not in text

    def test_provider_verify_recovery_code(self):
        provider = TOTPProvider()
        enrollment = provider.enroll("frank@example.com")
        first = enrollment.recovery_codes[0]
        matched = provider.verify_recovery_code(
            first, list(enrollment.recovery_code_hashes)
        )
        assert matched == hash_recovery_code(first)
