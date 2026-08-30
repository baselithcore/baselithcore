"""OpenInference span enrichment for LLM-observability backends.

The OTel spans carry ``gen_ai.*`` semconv attributes; Phoenix/Arize-style
backends key on OpenInference attributes instead. Opt-in enrichment
(``BASELITH_OPENINFERENCE_ENABLED``) adds them to the same spans, so pointing
the OTLP exporter at such a backend needs no separate pipeline. Prompt and
completion text is a second, separate opt-in
(``BASELITH_OPENINFERENCE_CAPTURE_CONTENT``) — content capture is a privacy
decision, not an observability default.
"""

from __future__ import annotations

from core.observability import (  # package re-exports: house convention
    MAX_CONTENT_CHARS,
    openinference_llm_attributes,
)


def test_disabled_by_default_returns_no_attributes(monkeypatch):
    monkeypatch.delenv("BASELITH_OPENINFERENCE_ENABLED", raising=False)
    attrs = openinference_llm_attributes(model="gpt-4o-mini", provider="openai")
    assert attrs == {}


def test_enabled_emits_llm_span_kind_and_identity(monkeypatch):
    monkeypatch.setenv("BASELITH_OPENINFERENCE_ENABLED", "true")
    attrs = openinference_llm_attributes(
        model="gpt-4o-mini",
        provider="openai",
        input_tokens=120,
        output_tokens=42,
    )
    assert attrs["openinference.span.kind"] == "LLM"
    assert attrs["llm.model_name"] == "gpt-4o-mini"
    assert attrs["llm.provider"] == "openai"
    assert attrs["llm.token_count.prompt"] == 120
    assert attrs["llm.token_count.completion"] == 42
    assert attrs["llm.token_count.total"] == 162


def test_content_not_captured_without_second_opt_in(monkeypatch):
    monkeypatch.setenv("BASELITH_OPENINFERENCE_ENABLED", "true")
    monkeypatch.delenv("BASELITH_OPENINFERENCE_CAPTURE_CONTENT", raising=False)
    attrs = openinference_llm_attributes(
        model="m", provider="p", prompt="secret question", completion="answer"
    )
    assert "input.value" not in attrs
    assert "output.value" not in attrs


def test_content_captured_and_truncated_with_opt_in(monkeypatch):
    monkeypatch.setenv("BASELITH_OPENINFERENCE_ENABLED", "true")
    monkeypatch.setenv("BASELITH_OPENINFERENCE_CAPTURE_CONTENT", "true")
    long_prompt = "q" * (MAX_CONTENT_CHARS + 500)
    attrs = openinference_llm_attributes(
        model="m", provider="p", prompt=long_prompt, completion="the answer"
    )
    assert attrs["input.value"] == "q" * MAX_CONTENT_CHARS
    assert attrs["output.value"] == "the answer"


def test_token_counts_omitted_when_unknown(monkeypatch):
    monkeypatch.setenv("BASELITH_OPENINFERENCE_ENABLED", "true")
    attrs = openinference_llm_attributes(model="m", provider="p")
    assert "llm.token_count.prompt" not in attrs
    assert "llm.token_count.total" not in attrs
