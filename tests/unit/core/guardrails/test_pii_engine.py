"""PII redaction engine seam + EU pattern coverage.

Redaction was 5 US-centric regexes. Two upgrades:

* the regex set gains EU patterns (IBAN, Italian codice fiscale);
* an optional NER engine seam (``BASELITH_PII_ENGINE=presidio``, extra
  ``baselith-core[pii]``) lets Presidio replace the regex pass in
  ``OutputGuard`` — with the regexes as the always-on fallback when the
  engine is unavailable.
"""

from __future__ import annotations

import pytest
from core.guardrails.pii import get_pii_engine

from core.guardrails import pii as pii_module
from core.guardrails.output_guard import OutputGuard


def test_iban_is_redacted_by_default_regexes():
    guard = OutputGuard()
    result = guard.filter("wire the funds to IT60X0542811101000000123456 today")
    assert "IT60X0542811101000000123456" not in result.filtered_output
    assert result.redactions and "iban" in result.redactions


def test_italian_codice_fiscale_is_redacted():
    guard = OutputGuard()
    result = guard.filter("the taxpayer is RSSMRA85M01H501Z as filed")
    assert "RSSMRA85M01H501Z" not in result.filtered_output
    assert result.redactions and "codice_fiscale" in result.redactions


def test_ordinary_uppercase_words_are_not_flagged_as_iban():
    guard = OutputGuard()
    result = guard.filter("the HTTP2 protocol and the ISO27001 standard")
    assert result.filtered_output == "the HTTP2 protocol and the ISO27001 standard"


def test_engine_off_without_env(monkeypatch):
    monkeypatch.delenv("BASELITH_PII_ENGINE", raising=False)
    get_pii_engine.cache_clear()
    assert get_pii_engine() is None
    get_pii_engine.cache_clear()


def test_unknown_engine_name_is_off(monkeypatch):
    monkeypatch.setenv("BASELITH_PII_ENGINE", "acme-ner")
    get_pii_engine.cache_clear()
    assert get_pii_engine() is None
    get_pii_engine.cache_clear()


class _FakeEngine:
    def __init__(self) -> None:
        self.calls = 0

    def redact(self, text: str) -> tuple[str, dict[str, int]]:
        self.calls += 1
        return text.replace("Mario Rossi", "[PERSON_REDACTED]"), {"person": 1}


def test_output_guard_uses_configured_engine(monkeypatch):
    engine = _FakeEngine()
    monkeypatch.setattr(pii_module, "get_pii_engine", lambda: engine)

    result = OutputGuard().filter("the report was written by Mario Rossi.")

    assert "[PERSON_REDACTED]" in result.filtered_output
    assert result.redactions == {"person": 1}
    assert engine.calls == 1


def test_output_guard_falls_back_to_regex_on_engine_error(monkeypatch):
    class _Broken:
        def redact(self, text: str):
            raise RuntimeError("model not loaded")

    monkeypatch.setattr(pii_module, "get_pii_engine", lambda: _Broken())

    result = OutputGuard().filter("mail me at leak@example.com")

    assert "leak@example.com" not in result.filtered_output
    assert result.redactions and "email" in result.redactions


def test_presidio_engine_selected_but_not_installed_is_off(monkeypatch):
    monkeypatch.setenv("BASELITH_PII_ENGINE", "presidio")
    get_pii_engine.cache_clear()
    if pytest.importorskip("importlib.util").find_spec("presidio_analyzer"):
        pytest.skip("presidio installed in this environment")
    assert get_pii_engine() is None
    get_pii_engine.cache_clear()
