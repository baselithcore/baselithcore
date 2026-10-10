"""Exact-output tests for ``redact_secrets`` / ``safe_snippet``.

These mask credentials a third party echoes back (provider error bodies,
exception text). Assertions pin the whole output: a redaction that keeps one
character of the secret, drops the JSON quotes or swallows the key is as much
a regression as one that stops matching.
"""

from __future__ import annotations

import pytest

from core.observability.redaction import redact_secrets, safe_snippet


class TestKnownSecrets:
    def test_known_secret_masked_anywhere(self):
        assert redact_secrets("err: xSEKRETx", ["SEKRET"]) == "err: x[REDACTED]x"

    def test_custom_mask_applies_to_known_secrets(self):
        assert redact_secrets("a SEKRET b", ["SEKRET"], mask="***") == "a *** b"

    def test_empty_secret_is_ignored(self):
        assert redact_secrets("plain text", [""]) == "plain text"


class TestKeyValuePairs:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("token=abc123", "token=[REDACTED]"),
            ("refresh_token: r-1 next", "refresh_token: [REDACTED] next"),
            ("client_secret = s3cr3t&x=1", "client_secret = [REDACTED]&x=1"),
            ("Authorization: Bearer abc.def", "Authorization: [REDACTED]"),
            ("X-Api-Key=k-1;", "X-Api-Key=[REDACTED];"),
            ("code=4/0AX-auth", "code=[REDACTED]"),
            # Numeric-looking credentials are still credentials (OTP, PIN).
            ("token=123456", "token=[REDACTED]"),
            ("password='1234'", "password='[REDACTED]'"),
        ],
    )
    def test_value_masked_key_and_separator_kept(self, text, expected):
        assert redact_secrets(text) == expected

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ('{"access_token": "ya29x"}', '{"access_token": "[REDACTED]"}'),
            ('{"client_secret":"abc"}', '{"client_secret":"[REDACTED]"}'),
            ("{'secret': 'xyz'}", "{'secret': '[REDACTED]'}"),
            ('{"password": "a\\"b"}', '{"password": "[REDACTED]"}'),
        ],
    )
    def test_json_form_keeps_quotes(self, text, expected):
        assert redact_secrets(text) == expected

    @pytest.mark.parametrize(
        "text", ["code=404", 'error "code": "500"', "CODE: 429", "error_code=abc"]
    )
    def test_status_code_is_not_a_credential(self, text):
        assert redact_secrets(text) == text

    def test_custom_mask_in_pairs(self):
        assert redact_secrets('token=t "secret": "s"', mask="#") == (
            'token=# "secret": "#"'
        )


class TestStandaloneTokens:
    def test_bare_bearer_token(self):
        assert redact_secrets("sent Bearer abcdefgh123 ok") == (
            "sent Bearer [REDACTED] ok"
        )

    @pytest.mark.parametrize(
        "token",
        [
            "eyJhbGciOiJI.eyJzdWIiOiIx.c2lnbmF0dXJl",
            "ya29.a0AfH6SMBx-abcdef",
            "1//0gREFRESH-token-abcdefgh",
        ],
    )
    def test_jwt_and_google_tokens(self, token):
        assert redact_secrets(f"echo {token} end") == "echo [REDACTED] end"

    def test_custom_mask_on_bearer_and_jwt(self):
        out = redact_secrets("Bearer abcdefgh123 eyJhbGciOiJI.eyJzdWIiOiIx.x", mask="#")
        assert out == "Bearer # #"


class TestSafeSnippet:
    def test_default_limit_is_200(self):
        assert len(safe_snippet("a" * 500)) == 200

    def test_control_chars_collapse_to_single_spaces(self):
        assert safe_snippet("a\nb\r\n\tc   d") == "a b c d"

    def test_redacts_before_cutting(self):
        # The secret straddles the limit: no readable prefix may survive.
        out = safe_snippet("token=" + "s" * 300, limit=12)
        assert out == "token=[REDAC"

    def test_known_secrets_and_mask_are_forwarded(self):
        assert safe_snippet("x SEK y", ["SEK"], mask="#") == "x # y"

    def test_reads_at_most_4096_chars(self):
        out = safe_snippet("a" * 4096 + " token=leak", limit=10_000)
        assert out == "a" * 4096
