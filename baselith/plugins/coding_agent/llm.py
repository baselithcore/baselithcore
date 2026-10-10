"""Asking the model for a typed answer, with a plain-text fallback.

The first choice is the service's public ``generate(response_format=...)``: the schema travels with
the call and an answer that does not validate is retried once with the error. A provider or model that cannot
produce schema-bound JSON gets one plain-text call instead, whose fenced blocks are read into the
same model (first block: the code or the tests; second block: the tests). The caller always gets
a validated instance or an exception, never raw text.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from pydantic import BaseModel

from baselith.core.observability.logging import get_logger

from .prompts import SYSTEM_PROMPT

logger = get_logger(__name__)

M = TypeVar("M", bound=BaseModel)
TextAsk = Callable[[str], Awaitable[str]]

_FENCE = re.compile(r"```[\w+-]*\n(.*?)```", re.DOTALL)

_FALLBACK_HINTS: dict[str, str] = {
    "CodeAnswer": "Answer with the complete code in one fenced block, then one short paragraph.",
    "CodeWithTests": (
        "Answer with two fenced blocks: first the complete code (solution.py), then the complete "
        "pytest file; then one short paragraph."
    ),
    "TestsAnswer": "Answer with the complete pytest file in one fenced block, then one sentence.",
    "WorkspaceFix": (
        "Answer with two fenced blocks: first the complete fixed file, then the complete test "
        "file; then one short paragraph."
    ),
    "WorkspaceCode": "Answer with the complete fixed file in one fenced block, then one sentence.",
}


def _outside(text: str) -> str:
    return " ".join(_FENCE.sub(" ", text).split())[:600]


def parse_fallback(text: str, model: type[M]) -> M:
    """``text`` (a plain answer with fenced blocks) read into ``model``.

    Raises:
        ValueError: The answer holds fewer blocks than ``model`` needs.
    """
    blocks = [b.strip() + "\n" for b in _FENCE.findall(text or "")]
    explanation = _outside(text or "")
    name = model.__name__
    if name in ("CodeWithTests", "WorkspaceFix"):
        if len(blocks) < 2:
            raise ValueError("the answer did not hold both the code and the tests")
        return model.model_validate(
            {
                "code": blocks[0],
                "tests": blocks[1],
                "explanation": explanation,
                "changes": [],
            }
        )
    if not blocks:
        raise ValueError("the answer held no code block")
    if name == "TestsAnswer":
        return model.model_validate({"tests": blocks[0], "explanation": explanation})
    return model.model_validate(
        {"code": blocks[0], "explanation": explanation, "changes": []}
    )


def _json_payload(text: str) -> str:
    """The JSON object in ``text`` (models sometimes wrap it in a fence or a sentence)."""
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if 0 <= start < end else text


async def _schema_bound(
    llm: Any, prompt: str, model: type[M], *, retries: int, system: str = SYSTEM_PROMPT
) -> M:
    """``model`` from the public ``generate(response_format=...)``, retried on a bad answer.

    The public method is used rather than ``baselith.core.services.llm.typed.generate_typed`` (which
    reaches into ``LLMService`` internals), so the central funnel, a caller's scoped service
    and a test double all work alike.
    """
    from baselith.core.services.llm.tool_calling import ResponseFormat

    response_format = ResponseFormat(
        schema=model.model_json_schema(), name=model.__name__
    )
    attempt_prompt, last_error = prompt, ""
    for _ in range(retries + 1):
        result = await llm.generate(
            attempt_prompt,
            response_format=response_format,
            system_prompt=system,
            temperature=0.0,
        )
        text = getattr(result, "text", None) or getattr(result, "content", None) or ""
        try:
            return model.model_validate_json(_json_payload(str(text)))
        except ValueError as exc:
            last_error = str(exc)[:500]
            attempt_prompt = (
                f"{prompt}\n\nYour previous answer did not match the schema ({last_error}). "
                "Answer again with only the JSON object."
            )
    raise ValueError(
        f"no valid {model.__name__} after {retries + 1} answers: {last_error}"
    )


async def ask_typed(
    llm: Any,
    prompt: str,
    model: type[M],
    *,
    ask_text: TextAsk,
    system: str = SYSTEM_PROMPT,
) -> M:
    """A validated ``model`` for ``prompt``: schema-bound first, plain text second.

    Args:
        llm: The LLM service (the agent's injected client or the central funnel).
        prompt: The task prompt (data already fenced).
        model: The Pydantic answer shape.
        ask_text: The agent's plain-text call, used for the fallback.
        system: The system prompt of the schema-bound call.
    """
    try:
        return await _schema_bound(llm, prompt, model, retries=1, system=system)
    except Exception as exc:
        logger.warning(
            "coding_typed_fallback", model=model.__name__, error_type=type(exc).__name__
        )
    hint = _FALLBACK_HINTS.get(model.__name__, "")
    return parse_fallback(await ask_text(f"{prompt}\n\n{hint}"), model)


__all__ = ["ask_typed", "parse_fallback"]
