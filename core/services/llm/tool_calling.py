"""Provider-agnostic tool-calling and structured-output primitives.

The LLM stack historically spoke ``prompt: str -> str``. Native tool calling and
structured outputs need a richer contract: a request may carry tool
specifications plus a tool-choice policy, and a response may carry zero or more
structured tool-call objects (each with a provider-assigned id and a parsed JSON
argument object) alongside or instead of text.

These types are the *neutral* representation. Each provider adapter
(anthropic/openai/ollama/...) maps them to and from its own wire shape, so
call-sites and the orchestration layer never depend on a provider SDK.

Tool schemas reuse the JSON-Schema ``input_schema`` already carried by
``core.mcp.types.MCPTool`` — an MCP tool becomes an :class:`LLMToolSpec` with no
reshaping (see :func:`tool_spec_from_mcp`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from core.services.llm.messages import Message
from core.services.llm.usage import Usage

if TYPE_CHECKING:
    from core.mcp.types import MCPTool

ToolChoiceMode = Literal["auto", "any", "none", "tool"]


@dataclass(slots=True)
class ToolChoice:
    """Provider-agnostic tool-selection policy.

    Attributes:
        mode: ``auto`` (model decides), ``any`` (must call some tool),
            ``none`` (tools visible but not callable), or ``tool`` (force the
            named tool — ``name`` must be set).
        name: Target tool name when ``mode == "tool"``.
    """

    mode: ToolChoiceMode = "auto"
    name: str | None = None

    def __post_init__(self) -> None:
        if self.mode == "tool" and not self.name:
            raise ValueError("ToolChoice(mode='tool') requires a tool name")
        if self.mode != "tool" and self.name is not None:
            raise ValueError("ToolChoice.name is only valid with mode='tool'")

    @classmethod
    def forced(cls, name: str) -> ToolChoice:
        """Force the model to call the tool named ``name``."""
        return cls(mode="tool", name=name)


# Convenience singletons for the common policies.
AUTO = ToolChoice(mode="auto")
ANY = ToolChoice(mode="any")
NONE = ToolChoice(mode="none")


@dataclass(slots=True)
class LLMToolSpec:
    """A single tool exposed to the model.

    Attributes:
        name: Identifier the model uses to call the tool.
        description: What the tool does and, ideally, *when* to call it —
            providers rely on this to decide invocation.
        parameters: JSON-Schema object describing the tool's arguments. Must be
            a ``{"type": "object", ...}`` schema; reused verbatim as the
            provider ``input_schema`` / function ``parameters``.
        strict: When True, request strict schema enforcement where the provider
            supports it (adds ``additionalProperties: false`` semantics so the
            emitted arguments validate exactly).
        annotations: Behavioural hints about the tool, in MCP's vocabulary
            (``readOnlyHint``, ``destructiveHint``). Derived from the tool's
            autonomy category so the *same* fact that drives the approval gate
            is visible to whatever renders or reviews the tool list. Neutral
            metadata only: no provider takes an ``annotations`` field on a tool
            definition today, so it is deliberately not sent on the wire.
    """

    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=lambda: {"type": "object"})
    strict: bool = False
    annotations: dict[str, Any] | None = None


@dataclass(slots=True)
class ResponseFormat:
    """Structured-output constraint: force the response to match a JSON Schema.

    Maps to Anthropic ``output_config.format`` (json_schema) and OpenAI
    ``response_format`` (json_schema). Providers without json_schema support
    degrade to plain JSON mode plus the schema described in the prompt.

    Attributes:
        schema: JSON-Schema object the response must satisfy.
        name: Schema label some providers require (OpenAI json_schema.name).
        strict: Request exact schema enforcement where supported.
    """

    schema: dict[str, Any]
    name: str = "response"
    strict: bool = True


@dataclass(slots=True)
class ToolCall:
    """A structured tool invocation emitted by the model.

    Attributes:
        id: Provider-assigned call id; echoed back in the tool result so the
            model can correlate multi-tool turns. May be synthesized for
            providers that don't assign one.
        name: Name of the tool the model chose to call.
        arguments: Parsed JSON arguments (never the raw string — callers must
            not re-parse).
    """

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LLMResult:
    """Structured envelope returned by the native/structured generation path.

    A turn may carry text, tool calls, or both. ``generate_response`` (the
    legacy ``-> str`` API) wraps this and returns ``text or ""``.

    Attributes:
        text: Assistant text, if any.
        tool_calls: Structured tool invocations the model requested.
        stop_reason: Normalized stop reason (``end_turn``, ``max_tokens``,
            ``stop_sequence``, ``tool_use``, ``pause_turn``, ``refusal``) when
            the provider reports one.
        tokens_used: Total tokens attributed to the call (every billed
            bucket). Kept for backward compatibility; prefer :attr:`usage`,
            which carries the split needed to price the call.
        usage: Per-bucket token accounting for the call.
        stop_details: Provider payload attached to the stop reason; populated
            for a refusal (``category``, ``explanation``).
        truncated: True when the answer was cut short by the output cap
            (``stop_reason == "max_tokens"``), so a consumer knows not to
            parse it as complete.
        native: True when produced by a provider's native tool API; False when
            produced by the prompt-coercion fallback.
        raw: Provider-native response object, retained for debugging/tracing.
            Excluded from equality and repr.
        message: The assistant turn exactly as the provider produced it —
            thinking blocks included — for a loop that replays the history
            rather than rebuilding a prompt. Set by the message API; ``None``
            on the legacy paths, where
            :func:`core.services.llm.messages.message_from_result`
            reconstructs it from ``raw`` or from ``text``/``tool_calls``.
    """

    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str | None = None
    tokens_used: int = 0
    native: bool = True
    raw: Any = field(default=None, repr=False, compare=False)
    usage: Usage = field(default_factory=Usage)
    stop_details: dict[str, Any] | None = None
    truncated: bool = False
    message: Message | None = None

    def __post_init__(self) -> None:
        """Keep ``tokens_used`` and ``usage`` consistent for either spelling.

        Callers (and a decade of test doubles) build results with
        ``tokens_used=N`` alone; providers now fill ``usage``. Whichever side
        is given wins, so neither construction style has to know about the
        other.
        """
        if self.tokens_used <= 0 and not self.usage.is_empty:
            self.tokens_used = self.usage.total

    @property
    def has_tool_calls(self) -> bool:
        """True when the model requested at least one tool call."""
        return bool(self.tool_calls)


def tool_spec_from_mcp(tool: MCPTool) -> LLMToolSpec:
    """Adapt an :class:`~core.mcp.types.MCPTool` to an :class:`LLMToolSpec`.

    The MCP ``input_schema`` is already a JSON-Schema object, so it maps to
    ``parameters`` unchanged.
    """
    return LLMToolSpec(
        name=tool.name,
        description=tool.description,
        parameters=tool.input_schema or {"type": "object"},
    )


__all__ = [
    "ANY",
    "AUTO",
    "NONE",
    "LLMResult",
    "LLMToolSpec",
    "ResponseFormat",
    "ToolCall",
    "ToolChoice",
    "Message",
    "ToolChoiceMode",
    "Usage",
    "tool_spec_from_mcp",
]
