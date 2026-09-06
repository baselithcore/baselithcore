"""Credential-shaped tokens are redacted by the always-on regex layer."""

from __future__ import annotations

import pytest

from core.guardrails.output_guard import OutputGuard

SECRETS = [
    ("aws_access_key", "AKIAIOSFODNN7EXAMPLE"),
    ("api_key_prefixed", "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789ABCD"),
    ("github_token", "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij123456"),
    # The public jwt.io example token; allowlisted verbatim in .gitleaks.toml.
    (
        "jwt",
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
    ),
    (
        "private_key_block",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----",
    ),
]


@pytest.mark.parametrize(("kind", "secret"), SECRETS)
def test_secret_is_redacted(kind: str, secret: str) -> None:
    result = OutputGuard().filter(f"here it is: {secret} - keep it safe")
    assert secret not in result.filtered_output
    assert result.redactions and kind in result.redactions
    assert f"[{kind.upper()}_REDACTED]" in result.filtered_output


@pytest.mark.parametrize(
    "text",
    [
        "The skeleton key pattern is described in sk-learn's docs.",
        "AKIA is a prefix, not a key on its own.",
        "ghp_short is not a token",
        "eyJ is how every base64 JSON starts",
    ],
)
def test_ordinary_text_is_not_redacted_as_secret(text: str) -> None:
    result = OutputGuard().filter(text)
    assert result.filtered_output == text
