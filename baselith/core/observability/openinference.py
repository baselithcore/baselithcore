"""OpenInference span enrichment for LLM-observability backends.

The LLM spans carry OTel ``gen_ai.*`` semantic-convention attributes;
OpenInference-based backends (Arize Phoenix and friends) key on their own
attribute names instead (``openinference.span.kind``, ``llm.model_name``,
``llm.token_count.*``, ``input.value``/``output.value``). This module emits
those attributes onto the SAME spans, opt-in, so pointing the existing OTLP
exporter (``OTEL_EXPORTER_OTLP_ENDPOINT``) at such a backend needs no second
telemetry pipeline.

Two independent switches:

* ``BASELITH_OPENINFERENCE_ENABLED`` — identity/token attributes.
* ``BASELITH_OPENINFERENCE_CAPTURE_CONTENT`` — additionally capture prompt
  and completion text (truncated). Content capture is a privacy decision,
  never an observability default: prompts routinely carry user PII, and span
  storage outlives the request.
"""

from __future__ import annotations

import os
from typing import Any

_ENABLED_ENV = "BASELITH_OPENINFERENCE_ENABLED"
_CONTENT_ENV = "BASELITH_OPENINFERENCE_CAPTURE_CONTENT"
_TRUTHY = ("1", "true", "yes", "on")

#: Cap on captured prompt/completion text per span attribute.
MAX_CONTENT_CHARS = 4096


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def openinference_enabled() -> bool:
    """Whether OpenInference enrichment is active for this process."""
    return _flag(_ENABLED_ENV)


def openinference_llm_attributes(
    *,
    model: str,
    provider: str | None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    prompt: str | None = None,
    completion: str | None = None,
) -> dict[str, Any]:
    """OpenInference attributes for one LLM span (empty dict when disabled).

    Args:
        model: The resolved model name.
        provider: The serving provider.
        input_tokens: Prompt token count, when known.
        output_tokens: Completion token count, when known.
        prompt: Prompt text — captured (truncated) only under the content
            opt-in.
        completion: Completion text — same gating as ``prompt``.

    Returns:
        Attribute mapping to set on the span; ``{}`` when enrichment is off.
    """
    if not openinference_enabled():
        return {}
    attrs: dict[str, Any] = {
        "openinference.span.kind": "LLM",
        "llm.model_name": model,
        "llm.provider": provider or "unknown",
    }
    if input_tokens is not None:
        attrs["llm.token_count.prompt"] = input_tokens
    if output_tokens is not None:
        attrs["llm.token_count.completion"] = output_tokens
    if input_tokens is not None and output_tokens is not None:
        attrs["llm.token_count.total"] = input_tokens + output_tokens
    if _flag(_CONTENT_ENV):
        if prompt is not None:
            attrs["input.value"] = prompt[:MAX_CONTENT_CHARS]
        if completion is not None:
            attrs["output.value"] = completion[:MAX_CONTENT_CHARS]
    return attrs


__all__ = [
    "MAX_CONTENT_CHARS",
    "openinference_enabled",
    "openinference_llm_attributes",
]
