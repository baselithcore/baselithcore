"""Synchronous groundedness rail for the outbound guard pipeline.

Opt-in via ``BASELITH_OUTPUT_GROUNDEDNESS`` (``off`` default | ``annotate`` |
``block``). When a result dict carries retrieved source material (``sources``
or ``context``, non-empty), the response is judged against it by
:class:`core.evaluation.judges.FaithfulnessEvaluator` before it leaves the
orchestrator — one extra LLM call per sourced response, which is why the
default is off.

``annotate`` surfaces ``{score, should_refine}`` under
``result["guardrails"]["groundedness"]``; ``block`` additionally replaces an
ungrounded response (score below ``BASELITH_OUTPUT_GROUNDEDNESS_THRESHOLD``,
default 0.6) with a refusal-to-assert message and emits the standard
guardrail block metric. Judge failures — exceptions and the evaluator's own
score-0 fallback verdict — are strictly fail-open: annotate nothing, log.

Split out of :mod:`core.orchestration.guard_pipeline` for the module size cap;
the pipeline's :func:`guard_output_async` is the only intended caller.
"""

from __future__ import annotations

import os
import time
from typing import Any

from core.observability.logging import get_logger
from core.observability.metrics import (
    GUARDRAIL_BLOCKS_TOTAL,
    GUARDRAIL_LATENCY_SECONDS,
)

logger = get_logger(__name__)

_MODE_ENV = "BASELITH_OUTPUT_GROUNDEDNESS"
_THRESHOLD_ENV = "BASELITH_OUTPUT_GROUNDEDNESS_THRESHOLD"
_THRESHOLD_DEFAULT = 0.6
#: Result keys that may carry retrieved source material, in lookup order.
_SOURCE_KEYS = ("sources", "context")
_REFUSAL = (
    "I could not verify this response against the retrieved sources, so I am "
    "not asserting it. Please rephrase the question or provide more context."
)


def groundedness_mode() -> str:
    """Resolve the configured mode; unknown values degrade to ``off``."""
    mode = os.environ.get(_MODE_ENV, "off").strip().lower()
    return mode if mode in ("off", "annotate", "block") else "off"


def _threshold() -> float:
    """Blocking threshold; malformed env values fall back to the default."""
    try:
        return float(os.environ.get(_THRESHOLD_ENV, ""))
    except ValueError:
        return _THRESHOLD_DEFAULT


def _sources_text(result: dict[str, Any]) -> str | None:
    """Flatten the result's retrieved source material into judge context.

    ``sources`` entries may be plain strings or retrieval dicts (``content`` /
    ``text`` / ``snippet``); ``context`` is typically the raw RAG context
    string. Returns None when the result carries no source material.
    """
    for key in _SOURCE_KEYS:
        value = result.get(key)
        if not value:
            continue
        if isinstance(value, str):
            return value
        if isinstance(value, (list, tuple)):
            parts: list[str] = []
            for item in value:
                if isinstance(item, dict):
                    text = (
                        item.get("content") or item.get("text") or item.get("snippet")
                    )
                    parts.append(str(text) if text else str(item))
                else:
                    parts.append(str(item))
            flattened = "\n\n".join(part for part in parts if part)
            if flattened:
                return flattened
            continue
        return str(value)
    return None


def _build_judge() -> Any:
    """Judge factory — the seam tests replace with a mocked evaluator."""
    from core.evaluation.judges import FaithfulnessEvaluator

    return FaithfulnessEvaluator()


async def apply_groundedness(result: dict[str, Any]) -> dict[str, Any]:
    """Judge the result's response against its retrieved sources (in place).

    No-op when the mode is ``off``, the response is not a non-empty string,
    or the result carries no source material. See the module docstring for
    the ``annotate``/``block`` semantics and the fail-open policy.

    Args:
        result: Orchestrator result dict (mutated in place).

    Returns:
        The same result dict, possibly annotated and/or blocked.
    """
    mode = groundedness_mode()
    if mode == "off":
        return result
    response = result.get("response")
    if not isinstance(response, str) or not response:
        return result
    sources = _sources_text(result)
    if not sources:
        return result

    started = time.perf_counter()
    try:
        judge = _build_judge()
        verdict = await judge.evaluate(
            response, str(result.get("query", "")), {"memory_context": sources}
        )
    except Exception as exc:
        logger.warning("groundedness_judge_failed_open", extra={"error": str(exc)})
        return result
    finally:
        GUARDRAIL_LATENCY_SECONDS.labels(layer="output_groundedness").observe(
            time.perf_counter() - started
        )
    if verdict.metadata.get("fallback"):
        # BaseLLMEvaluator swallows LLM outages into a score-0 fallback
        # verdict; treating that as a real zero would block on every outage.
        logger.warning("groundedness_judge_fallback_fail_open")
        return result

    meta = result.setdefault("guardrails", {})
    grounded: dict[str, Any] = {
        "score": verdict.score,
        "should_refine": verdict.should_refine,
    }
    meta["groundedness"] = grounded
    if mode == "block" and verdict.score < _threshold():
        GUARDRAIL_BLOCKS_TOTAL.labels(
            layer="output_groundedness", reason="ungrounded"
        ).inc()
        logger.warning(
            "orchestrator_output_blocked_ungrounded",
            extra={"score": verdict.score, "threshold": _threshold()},
        )
        grounded["blocked"] = True
        result["response"] = _REFUSAL
    return result


__all__ = ["apply_groundedness", "groundedness_mode"]
