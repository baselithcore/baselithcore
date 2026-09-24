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

__all__ = ["THINK_CLOSE", "THINK_OPEN", "ReasoningStreamFilter", "strip_reasoning"]

THINK_OPEN = "<think>"
THINK_CLOSE = "</think>"

#: How much answer text a stream holds back while waiting to learn whether it
#: opened with reasoning. Past this, it is treated as reasoning-free and flows.
DEFAULT_MAX_BUFFER = 64_000


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

    def __init__(self, max_buffer: int = DEFAULT_MAX_BUFFER) -> None:
        self._max_buffer = max_buffer
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
