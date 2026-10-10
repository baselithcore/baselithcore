"""Keep a thinking model's reasoning out of the answer text.

A thinking model (Qwen3, DeepSeek-R1, …) served by vLLM **without**
``--reasoning-parser`` returns its reasoning inside the answer. The chat
template opens the ``<think>`` block in the prompt, so the completion reads
``reasoning…</think>\\n\\nanswer`` — no opening tag, one closing tag — and every
caller shows the model thinking aloud before (or instead of) the answer. A JSON
caller fails to parse it outright.

The server flag is the real fix: with a parser, vLLM moves the reasoning to a
separate ``reasoning_content`` field and the answer arrives clean. These helpers
are the safety net for a server that lacks it, and are inert on one that has it
(the answer then carries no closing tag).
"""

from __future__ import annotations

import re

__all__ = [
    "THINK_CLOSE",
    "THINK_OPEN",
    "ReasoningStreamFilter",
    "can_stream_live",
    "strip_reasoning",
]

THINK_OPEN = "<think>"
THINK_CLOSE = "</think>"

#: How much answer text a stream holds back while waiting to learn whether it
#: opened with reasoning. Past this, it is treated as reasoning-free and flows.
DEFAULT_MAX_BUFFER = 64_000

#: Model families known NOT to reason inline. Only for these may a stream flow
#: from its first chunk: any other model might open its reasoning block in the
#: chat template (Qwen3, DeepSeek-R1, a custom ``--served-model-name``), so its
#: answer is held until ``</think>`` — never leaking reasoning is worth more
#: than a live stream, and the hold is exactly the pre-streaming behaviour.
_LIVE_SAFE = re.compile(
    r"llama|mistral|mixtral|gemma|phi-?\d|qwen-?2|glm-?4|granite|olmo|smollm"
    r"|command|^gpt-",
    re.I,
)
#: Inline reasoners, checked FIRST: a distilled or fine-tuned reasoner carries
#: its base family in its name (``deepseek-r1-distill-llama-70b``,
#: ``...-qwen2.5-...``), and a substring match on the family alone would stream
#: its ``<think>`` block to the client. Any of these markers keeps the hold.
_INLINE_REASONER = re.compile(
    r"(?<![a-z])r1(?![0-9])|deepseek-r|reason|think|qwq|qwen-?3|magistral"
    r"|exaone-deep|gpt-oss|marco-o1|sky-t1|openthinker|phi-?4-reasoning",
    re.I,
)


def can_stream_live(model: str | None, extra_pattern: str = "") -> bool:
    """Whether *model*'s text may stream without waiting for a ``</think>``.

    Args:
        model: The served model name.
        extra_pattern: Operator regex (``LLM_VLLM_LIVE_STREAM_MODELS``) naming
            further models known not to reason inline.
    """
    if not model:
        return False
    if _INLINE_REASONER.search(model):
        return False
    if _LIVE_SAFE.search(model):
        return True
    if extra_pattern:
        try:
            return re.search(extra_pattern, model, re.I) is not None
        except re.error:
            return False
    return False


def strip_reasoning(text: str | None) -> str:
    """*text* without a leading reasoning block.

    Args:
        text: A completion, possibly ``reasoning…</think>answer``.

    Returns:
        str: What follows the last ``</think>`` (left-stripped); ``""`` for a
        reasoning block the token cap cut off before it closed; otherwise
        *text* unchanged.
    """
    if not text:
        return ""
    if THINK_CLOSE in text:
        return text.rsplit(THINK_CLOSE, 1)[1].lstrip()
    if text.lstrip().startswith(THINK_OPEN):
        return ""
    return text


class ReasoningStreamFilter:
    """Streaming counterpart of :func:`strip_reasoning`.

    Holds text back until it knows whether the stream opened with reasoning:
    a ``</think>`` drops everything before it and lets the answer flow; the end
    of the stream (or ``max_buffer`` characters) without one releases the text
    untouched. Call :meth:`passthrough` when the server reports reasoning in a
    separate field — it is then parsing reasoning itself.
    """

    def __init__(
        self,
        max_buffer: int = DEFAULT_MAX_BUFFER,
        *,
        eager: bool = False,
    ) -> None:
        """Create a filter.

        Args:
            max_buffer: Characters held while waiting for a closing tag.
            eager: Release a stream the moment it is clear it does not
                *open* with a ``<think>`` tag, instead of waiting for a closing
                tag — what lets a model that does not reason inline stream
                live. False keeps the hold-until-close behaviour that a
                template-opened reasoner needs.
        """
        self._max_buffer = max_buffer
        self._eager = eager
        self._buffer = ""
        self._probing = True
        self._trim_lead = False

    def passthrough(self) -> str:
        """Stop filtering; returns whatever was held back."""
        held, self._buffer = self._buffer, ""
        self._probing = False
        return held

    def feed(self, chunk: str) -> str:
        """Take one streamed piece; return the text safe to emit now."""
        if not chunk:
            return ""
        if not self._probing:
            return self._lead(chunk)
        self._buffer += chunk
        if THINK_CLOSE in self._buffer:
            after = self._buffer.rsplit(THINK_CLOSE, 1)[1]
            self._buffer = ""
            self._probing = False
            self._trim_lead = True
            return self._lead(after)
        if len(self._buffer) > self._max_buffer:
            return self.passthrough()
        if self._eager:
            head = self._buffer.lstrip()
            # Not (the start of) an opening tag, so it cannot be reasoning.
            if head and not (
                head.startswith(THINK_OPEN) or THINK_OPEN.startswith(head)
            ):
                return self.passthrough()
        return ""

    def finish(self) -> str:
        """End of stream: release held text unless it was unfinished reasoning."""
        if not self._probing:
            return ""
        held = self.passthrough()
        return "" if held.lstrip().startswith(THINK_OPEN) else held

    def _lead(self, text: str) -> str:
        """Drop the blank lines between ``</think>`` and the answer."""
        if not self._trim_lead:
            return text
        text = text.lstrip()
        if text:
            self._trim_lead = False
        return text
