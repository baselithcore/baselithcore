"""One credential vocabulary across every redaction pass.

Three passes mask credentials: structured keys (log kwargs, nested dicts, the
Sentry scrubber), ``key=value`` pairs inside log messages, and third-party
free text (``redact_secrets``, which the connectors reuse). Each used to carry
its own key list, and they drifted: ``pwd=…`` and ``private_key=…`` leaked from
log messages, and a JSON-shaped ``{"password": "x"}`` in a message was never
masked at all. The cases below run every name through every pass, pin the
quoting each form keeps, and pin the prose that must stay readable.
"""

from __future__ import annotations

import logging
import time

import pytest

from baselith.core.connectors.errors import redact as connector_redact
from baselith.core.observability import redaction as redmod
from baselith.core.observability import setup as setupmod
from baselith.core.observability.redaction import (
    _key_is_sensitive,
    _mask_text,
    redact_secrets,
    redact_sensitive,
    redact_url_credentials,
)
from baselith.core.observability.sentry import _before_send

NEW_KEYS = [
    "pwd",
    "private_key",
    "private-key",
    "privateKey",
    "client_secret",
    "client-secret",
    "clientSecret",
    "access_token",
    "access-token",
    "accessToken",
    "refresh_token",
    "refresh-token",
    "refreshToken",
    "secret_key",
    "secretKey",
    "x-api-key",
]


@pytest.fixture
def masking_on(monkeypatch):
    class _Cfg:
        log_masking_enabled = True

    monkeypatch.setattr(redmod, "get_app_config", lambda: _Cfg())


class TestEveryPassKnowsEveryName:
    @pytest.mark.parametrize("key", NEW_KEYS)
    def test_log_message(self, key):
        assert _mask_text(f"login {key}=hunter2 ok") == f"login {key}=[REDACTED] ok"

    @pytest.mark.parametrize("key", NEW_KEYS)
    def test_free_text(self, key):
        assert redact_secrets(f"{key}: hunter2") == f"{key}: [REDACTED]"

    @pytest.mark.parametrize("key", NEW_KEYS)
    def test_structured_key(self, key):
        assert _key_is_sensitive(key) is True

    @pytest.mark.parametrize("key", NEW_KEYS)
    def test_sentry(self, key):
        event = {"extra": {key: "hunter2", "harmless": 1}}
        assert _before_send(event, {})["extra"] == {key: "[REDACTED]", "harmless": 1}

    def test_connectors_reuse_the_shared_redaction(self):
        assert connector_redact("pwd=hunter2 private_key=k", ()) == (
            "pwd=*** private_key=***"
        )


class TestQuotedValuesInMessages:
    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ('{"password": "x"}', '{"password": "[REDACTED]"}'),
            ('"api_key":"sk-123"', '"api_key":"[REDACTED]"'),
            (
                "{'token': 'abc', 'user': 'bob'}",
                "{'token': '[REDACTED]', 'user': 'bob'}",
            ),
            (
                "{'headers': {'Authorization': 'Bearer abc'}}",
                "{'headers': {'Authorization': '[REDACTED]'}}",
            ),
            ("{'pwd': b'raw'}", "{'pwd': b'[REDACTED]'}"),
            ('{"pwd": b"raw"}', '{"pwd": b"[REDACTED]"}'),
            ("password=bob", "password=[REDACTED]"),
            ('{"secret": "a\\"b c"}', '{"secret": "[REDACTED]"}'),
            ("{'secret': 'it\\'s'}", "{'secret': '[REDACTED]'}"),
            # No closing quote (a truncated message): fail closed.
            ('cfg {"password": "abc def', 'cfg {"password": "[REDACTED]'),
        ],
    )
    def test_value_masked_quotes_kept(self, message, expected):
        assert _mask_text(message) == expected
        assert redact_secrets(message) == expected

    def test_runs_in_the_structlog_processor(self, masking_on):
        event = {"event": 'payload={"client_secret": "s3"} sent'}
        out = redact_sensitive(None, "info", event)["event"]
        assert out == 'payload={"client_secret": "[REDACTED]"} sent'


class TestWhitespaceSeparatedValues:
    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("password hunter2 used", "password [REDACTED] used"),
            ("with token abc123", "with token [REDACTED]"),
            ("Authorization Basic dXNlcjpwYXNz", "Authorization [REDACTED]"),
            ("sent Bearer abcdefgh ok", "sent Bearer [REDACTED] ok"),
        ],
    )
    def test_credential_shaped_value_masked(self, message, expected):
        assert _mask_text(message) == expected
        assert redact_secrets(message) == expected


class TestOAuthCodeInFreeText:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("HTTP status code 500", "HTTP status code 500"),
            (
                "exchange failed for code 4/0AX-abc",
                "exchange failed for code [REDACTED]",
            ),
            ('code="X42"', 'code="[REDACTED]"'),
        ],
    )
    def test_numeric_code_kept_credential_masked(self, text, expected):
        assert redact_secrets(text) == expected


class TestProseStaysReadable:
    @pytest.mark.parametrize(
        "text",
        [
            "the password policy requires twelve characters",
            "Please change your password soon.",
            "tokenizer=cl100k_base max_tokens=4096",
            "Generated 512 tokens with the tokenizer",
            "secretary: Jane Doe",
            "the secret santa list is out",
            "token expired, refreshing",
            "password-protected archive",
            "passwordless login enabled",
            "error_code=abc",
        ],
    )
    def test_untouched(self, text):
        assert _mask_text(text) == text
        assert redact_secrets(text) == text


class TestCamelCaseKeys:
    """A keyword ending at a camelCase hump is a word end, like ``_`` is."""

    @pytest.mark.parametrize(
        ("text", "secret"),
        [
            ("passwordHash=s3cr3tHash", "s3cr3tHash"),
            ("dbPasswordValue: hunter2", "hunter2"),
            ("apiKeyId=k-123", "k-123"),
            ("clientSecretRef=abc999", "abc999"),
        ],
    )
    def test_masked(self, text, secret):
        assert secret not in _mask_text(text)
        assert secret not in redact_secrets(text)

    @pytest.mark.parametrize(
        "text",
        ["tokenizerName=cl100k_base", "secretaryName: Jane", "passwordlessMode=on"],
    )
    def test_lowercase_continuation_is_not_a_word_end(self, text):
        assert _mask_text(text) == text
        assert redact_secrets(text) == text


class TestUrlCredentials:
    @pytest.mark.parametrize("value", [123, None, b"redis://:pw@h/0", 1.5, ["x"]])
    def test_non_string_is_withheld(self, value):
        assert redact_url_credentials(value) == "[REDACTED]"  # type: ignore[arg-type]

    def test_userinfo_without_a_host(self):
        assert redact_url_credentials("redis://u:pw@:6379/0") == "redis://:6379/0"


class TestStdlibFilterSharesTheMasking:
    def test_auth_scheme_and_json_are_masked(self, monkeypatch):
        class _Cfg:
            log_masking_enabled = True

        monkeypatch.setattr(setupmod, "_app_config", _Cfg())
        record = logging.LogRecord(
            "t", logging.INFO, __file__, 1, "%s and %s", None, None
        )
        record.args = ("Authorization: Bearer xyz.123", '{"pwd": "p"}')
        assert setupmod.SensitiveDataFilter().filter(record) is True
        assert record.getMessage() == (
            'Authorization: [REDACTED] and {"pwd": "[REDACTED]"}'
        )


class TestLinear:
    """Every pattern stays linear on adversarial text (ReDoS).

    Measured as CPU time for the full input against a tenth of it, best of
    three: linear code gives ~10, a quadratic pattern ~100. ``"a." * 50000``
    cost seconds per log entry before the email pattern was anchored to the
    start of its character run.
    """

    MAX_RATIO = 30.0
    CPU_CEILING_S = 5.0
    N = 100_000

    ADVERSARIAL = {
        "dotted_run": "a." * (N // 2),
        "dashed_words": ("-api-key" * N)[:N],
        "unclosed_quotes": ('password="' * N)[:N],
        "unclosed_single_quotes": ("token='" * N)[:N],
        "key_then_spaces": "password" + " " * N,
        "key_colon_then_spaces": "password:" + " " * N,
        "keyword_dense_word": ("token" * N)[:N] + "=x",
        "keyword_dense_no_separator": ("token_" * N)[:N],
        "spaced_keys": ("password " * N)[:N],
        "escapes": '"password": "' + "\\\\" * (N // 2),
        "scheme_runs": ("Bearer " * N)[:N],
        "shaped_lookahead": "token " + "a" * N,
    }

    @staticmethod
    def _cpu(fn, text: str) -> float:
        timings = []
        for _ in range(3):
            started = time.process_time()
            fn(text)
            timings.append(time.process_time() - started)
        return min(timings)

    @pytest.mark.parametrize("fn", [_mask_text, redact_secrets])
    @pytest.mark.parametrize("text", ADVERSARIAL.values(), ids=ADVERSARIAL.keys())
    def test_scales_linearly(self, fn, text):
        tenth = self._cpu(fn, text[: len(text) // 10])
        full = self._cpu(fn, text)
        assert full < self.CPU_CEILING_S
        if tenth >= 0.002:
            assert full / tenth < self.MAX_RATIO, (full, tenth)
