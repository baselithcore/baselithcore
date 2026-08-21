"""Tests for :mod:`core.utils.logsafe`."""

from __future__ import annotations

import logging

import pytest

from core.utils.logsafe import DEFAULT_MAX_LENGTH, sanitize_log_value


class TestSanitizeLogValue:
    """Escaping and truncation of untrusted values headed for a log line."""

    def test_plain_value_is_unchanged(self) -> None:
        assert sanitize_log_value("baselithbot") == "baselithbot"

    @pytest.mark.parametrize(
        ("raw", "escaped"),
        [
            ("a\r\nb", "a\\x0d\\x0ab"),
            ("a\nb", "a\\x0ab"),
            ("a\rb", "a\\x0db"),
            ("a\tb", "a\\x09b"),
            ("a\x00b", "a\\x00b"),
            ("a\x1b[31mb", "a\\x1b[31mb"),
            ("a b", "a\\u2028b"),
        ],
    )
    def test_control_characters_are_escaped(self, raw: str, escaped: str) -> None:
        assert sanitize_log_value(raw) == escaped

    def test_forged_log_entry_stays_on_one_line(self) -> None:
        forged = "good\nERROR:root:Plugin evil verified"
        result = sanitize_log_value(forged)
        assert "\n" not in result
        assert result.startswith("good\\x0a")

    def test_printable_unicode_survives(self) -> None:
        assert sanitize_log_value("café-plugin ✓") == "café-plugin ✓"

    def test_non_string_values_are_rendered(self) -> None:
        assert sanitize_log_value(42) == "42"
        assert sanitize_log_value(None) == "None"

    def test_long_values_are_truncated_to_the_limit(self) -> None:
        result = sanitize_log_value("x" * (DEFAULT_MAX_LENGTH * 2))
        assert len(result) == DEFAULT_MAX_LENGTH
        assert result.endswith("...[truncated]")

    def test_custom_max_length(self) -> None:
        assert len(sanitize_log_value("x" * 100, max_length=20)) == 20

    def test_degenerate_max_length_never_raises(self) -> None:
        # Below the truncation marker's own length the value is simply cut.
        assert sanitize_log_value("abc", max_length=0) == "a"

    def test_escaping_happens_before_truncation(self) -> None:
        result = sanitize_log_value("\n" * 50, max_length=30)
        assert "\n" not in result


class TestSignatureLoggingIsInjectionSafe:
    """The signature gate must not emit a crafted plugin name verbatim."""

    def test_refusal_log_escapes_the_plugin_name(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        from core.plugins.signing import enforce_plugin_signature

        monkeypatch.setenv("BASELITH_REQUIRE_PLUGIN_SIGNATURES", "true")
        monkeypatch.setenv("BASELITH_PLUGIN_TRUST_ROOTS", "aa" * 32)

        with caplog.at_level(logging.ERROR):
            allowed = enforce_plugin_signature("evil\nERROR:forged", "abc", None)

        assert allowed is False
        assert all("\n" not in record.getMessage() for record in caplog.records)
        assert "evil\\x0aERROR:forged" in caplog.text
