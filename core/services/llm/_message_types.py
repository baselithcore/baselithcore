"""Neutral conversation types for the message-based agent loop.

Dataclasses only — no provider SDK, no I/O. The public import path is
:mod:`core.services.llm.messages`, which re-exports these alongside the wire
mappings in :mod:`core.services.llm._message_mapping`; they live apart so
neither module has to import the other (the mappings need the types) and both
stay well under the file-size cap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

__all__ = [
    "ContentBlock",
    "ImageBlock",
    "Message",
    "Role",
    "TextBlock",
    "ThinkingBlock",
    "ToolResultBlock",
    "ToolUseBlock",
]


#: Conversation roles. The system prompt is *not* a role here: every provider
#: this repo speaks to takes it as a separate request field (Anthropic
#: ``system=``, OpenAI a leading ``system`` message the adapter prepends), and
#: keeping it out of the history is what lets it carry a stable cache
#: breakpoint independent of how the conversation grows.
Role = Literal["user", "assistant"]


@dataclass(slots=True)
class TextBlock:
    """Plain text written by the user or the model.

    Attributes:
        text: The text itself.
    """

    text: str


@dataclass(slots=True)
class ImageBlock:
    """An image attached to a turn.

    Exactly one of ``data`` (base64) or ``url`` carries the image; ``data``
    wins when both are set.

    Attributes:
        data: Base64-encoded image bytes, without a data-URI prefix.
        media_type: IANA media type of ``data`` (``image/png``, ``image/jpeg``).
        url: Remote image URL, for providers that fetch it themselves.
    """

    data: str | None = None
    media_type: str = "image/png"
    url: str | None = None


@dataclass(slots=True)
class ToolUseBlock:
    """A tool invocation the model asked for.

    Attributes:
        id: Provider-assigned call id. The matching :class:`ToolResultBlock`
            echoes it, which is the only thing correlating a result to its
            call in a turn that requested several.
        name: Tool the model chose.
        input: Parsed JSON arguments (never a raw string).
    """

    id: str
    name: str
    input: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolResultBlock:
    """What a tool returned, addressed back to the call that requested it.

    Attributes:
        tool_use_id: The :attr:`ToolUseBlock.id` this answers.
        content: The rendered observation the model reads. Tool-controlled
            text arrives here already bounded, scanned and wrapped in the
            untrusted-content envelope by the loop's single rendering seam.
        is_error: True when the call failed. Providers that model it natively
            (Anthropic) receive the flag; the rest get it in the text, because
            a failure the model cannot see is a failure it will not correct.
    """

    tool_use_id: str
    content: str
    is_error: bool = False


@dataclass(slots=True)
class ThinkingBlock:
    """An opaque extended-thinking block, replayed exactly as received.

    The payload is deliberately not interpreted: it carries a cryptographic
    signature that the API validates when the block is sent back, so any
    normalisation — reordering keys is fine, rewriting text is not — risks a
    400 on the next turn. Providers without a thinking surface drop it.

    Attributes:
        payload: The provider's block, verbatim (``type``, ``thinking`` or
            ``data``, ``signature``).
    """

    payload: dict[str, Any] = field(default_factory=dict)


#: Anything that can appear inside a :class:`Message`.
ContentBlock = TextBlock | ImageBlock | ToolUseBlock | ToolResultBlock | ThinkingBlock


@dataclass(slots=True)
class Message:
    """One conversation turn.

    Attributes:
        role: ``user`` or ``assistant``.
        content: Ordered content blocks. A turn is a *list* of blocks, not a
            string: that is what lets one user message carry every tool result
            of a parallel turn, and one assistant message carry thinking, text
            and several tool calls at once.
    """

    role: Role
    content: list[ContentBlock] = field(default_factory=list)

    @classmethod
    def user(cls, text: str) -> Message:
        """A user turn containing a single text block."""
        return cls(role="user", content=[TextBlock(text=text)])

    @classmethod
    def assistant(cls, text: str) -> Message:
        """An assistant turn containing a single text block."""
        return cls(role="assistant", content=[TextBlock(text=text)])

    @classmethod
    def tool_results(cls, results: list[ToolResultBlock]) -> Message:
        """Every tool result of one turn, in a single user message.

        Splitting them across messages is not merely untidy: Anthropic rejects
        a conversation where an assistant turn's ``tool_use`` blocks are not
        all answered by the immediately following user turn.

        Args:
            results: The result blocks, in call order.

        Returns:
            Message: The user turn to append to the history.
        """
        return cls(role="user", content=list(results))

    @property
    def text(self) -> str:
        """Concatenated text of every :class:`TextBlock` in the turn."""
        return "".join(b.text for b in self.content if isinstance(b, TextBlock))

    @property
    def tool_uses(self) -> list[ToolUseBlock]:
        """Every tool call requested in this turn, in order."""
        return [b for b in self.content if isinstance(b, ToolUseBlock)]
