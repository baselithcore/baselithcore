"""Model-family capability table (thinking, sampling, effort, token defaults).

Anthropic's request surface is no longer uniform across families, and the
differences are *400 errors*, not degradations:

* Fable 5.x, Mythos 5.x, Opus 5 / 4.8 / 4.7 and Sonnet 5 accept only
  ``thinking: {"type": "adaptive"}`` and reject ``temperature`` / ``top_p`` /
  ``top_k`` outright;
* Opus 4.6 and Sonnet 4.6 take adaptive thinking *and* sampling params, with
  ``budget_tokens`` deprecated;
* Haiku 4.5 and older still require the ``{"type": "enabled",
  "budget_tokens": N}`` form for thinking, and none of them has an
  ``output_config.effort`` surface — though Haiku 4.5's output caps are this
  generation's, not the old 8192;
* output ceilings differ by generation and are enforced: 4096 on Claude 2 and
  Claude 3, 8192 on Claude 3.5, far more above that. The fallback profile
  carries the 4096 floor, because an unrecognised id is an unknown one and the
  default has to be the value no family refuses;
* ``output_config.effort`` gained ``xhigh``/``max`` on Opus 4.7+, Sonnet 5 and
  Fable 5.x only;
* Fable 5.1 and Mythos 5.1 reject a forced ``tool_choice``.

Encoding that as a lookup table — pure data, no I/O, no SDK import — keeps the
knowledge in one reviewable place instead of scattered ``if model.startswith``
branches in every provider method, and lets the same table serve the message
API added later.

Matching is by **longest** model-id prefix after normalisation, so
``claude-fable-5-1`` is served by its own row rather than the
``claude-fable-5`` one, and vendor-prefixed ids (Bedrock's
``us.anthropic.claude-opus-5-v1:0``) still resolve.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from core.observability.logging import get_logger

__all__ = [
    "EFFORT_LADDER",
    "ModelCapabilities",
    "ThinkingMode",
    "capabilities_for",
    "clamp_effort",
    "clamp_max_tokens",
    "configured_max_tokens",
    "default_max_tokens",
    "listed_capabilities_for",
    "rejects_forced_tool_choice",
    "supports_sampling_params",
]


class ThinkingMode(str, Enum):
    """How a model family accepts an extended-thinking request.

    Attributes:
        ADAPTIVE: ``thinking: {"type": "adaptive"}`` plus
            ``output_config.effort``; a ``budget_tokens`` request is a 400.
        BUDGET: the legacy ``{"type": "enabled", "budget_tokens": N}`` form.
        NONE: the family has no thinking surface at all.
    """

    ADAPTIVE = "adaptive"
    BUDGET = "budget"
    NONE = "none"


#: Effort tiers from strongest to weakest. Clamping walks down this ladder
#: until it finds a tier the target family actually supports.
EFFORT_LADDER: tuple[str, ...] = ("max", "xhigh", "high", "medium", "low")

logger = get_logger(__name__)

_BASE_EFFORTS = frozenset({"low", "medium", "high"})
_EXTENDED_EFFORTS = _BASE_EFFORTS | {"xhigh", "max"}

#: Anthropic's recommended output caps for the current families. Streaming is
#: required for large values, hence the two figures.
_DEFAULT_MAX_TOKENS = 16000
_DEFAULT_STREAM_MAX_TOKENS = 64000

#: Caps for the fallback row: the floor every Claude family has ever
#: supported. An unlisted id is an *unknown* id, so its default has to be the
#: value that cannot be refused — the Claude 3 generation (opus, sonnet,
#: haiku) and the Claude 2 family all top out at 4096 output tokens, and
#: streaming 8192 to one of them is a 400 rather than a longer answer.
_LEGACY_MAX_TOKENS = 4096
_LEGACY_STREAM_MAX_TOKENS = 4096

#: Caps for the legacy ids that genuinely reach 8192 (the 3.5 generation).
_LEGACY_8K_STREAM_MAX_TOKENS = 8192

#: Hard output ceilings, distinct from the recommended defaults above: asking
#: for more is a 400, not a longer answer. 128K on the 4.6+ generation, 64K on
#: Haiku 4.5, 8192 on the pre-4.6 ids.
_MAX_OUTPUT_TOKENS = 128000
_HAIKU_MAX_OUTPUT_TOKENS = 64000
_LEGACY_8K_MAX_OUTPUT_TOKENS = 8192
_LEGACY_MAX_OUTPUT_TOKENS = 4096


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    """What one model family accepts on the request.

    Attributes:
        thinking_mode: Which thinking payload the family accepts.
        supports_sampling_params: Whether ``temperature``/``top_p``/``top_k``
            may be sent at all (a 400 on the families that dropped them).
        supports_effort_levels: Accepted ``output_config.effort`` values.
        rejects_forced_tool_choice: True when ``tool_choice`` of ``any``/
            ``tool`` returns 400 (Fable 5.1 / Mythos 5.1).
        default_max_tokens: Output cap to send when the caller gave none.
        default_stream_max_tokens: Same, for a streaming request.
        max_output_tokens: The family's hard ceiling. Anything above it is
            rejected, so a request (or a thinking budget grown to fit one) is
            clamped to it rather than sent and refused.
    """

    thinking_mode: ThinkingMode = ThinkingMode.BUDGET
    supports_sampling_params: bool = True
    supports_effort_levels: frozenset[str] = _BASE_EFFORTS
    rejects_forced_tool_choice: bool = False
    default_max_tokens: int = _DEFAULT_MAX_TOKENS
    default_stream_max_tokens: int = _DEFAULT_STREAM_MAX_TOKENS
    max_output_tokens: int = _MAX_OUTPUT_TOKENS


#: Families that speak adaptive thinking and refuse sampling params.
_MODERN = ModelCapabilities(
    thinking_mode=ThinkingMode.ADAPTIVE,
    supports_sampling_params=False,
    supports_effort_levels=_EXTENDED_EFFORTS,
)
#: Same, plus the forced-tool_choice restriction of the ``.1`` releases.
_MODERN_NO_FORCED_TOOLS = ModelCapabilities(
    thinking_mode=ThinkingMode.ADAPTIVE,
    supports_sampling_params=False,
    supports_effort_levels=_EXTENDED_EFFORTS,
    rejects_forced_tool_choice=True,
)
#: The 4.6 generation: adaptive recommended, sampling still accepted, no xhigh.
_GEN_4_6 = ModelCapabilities(
    thinking_mode=ThinkingMode.ADAPTIVE,
    supports_sampling_params=True,
    supports_effort_levels=_BASE_EFFORTS,
)
#: Haiku 4.5: a current model on the older *request* contract — budget
#: thinking, sampling allowed — but with this generation's output caps. It has
#: no ``output_config.effort`` surface at all, so the effort set is empty and
#: a requested tier is dropped rather than clamped (clamping would still send
#: a field that errors).
_HAIKU_4_5 = ModelCapabilities(
    thinking_mode=ThinkingMode.BUDGET,
    supports_sampling_params=True,
    supports_effort_levels=frozenset(),
    max_output_tokens=_HAIKU_MAX_OUTPUT_TOKENS,
)
#: The 3.5 generation: budget thinking, sampling allowed, 8192 output. Listed
#: rather than inferred because only a row makes the ceiling enforceable —
#: ``clamp_max_tokens`` never acts on the fallback profile.
_LEGACY_8K = ModelCapabilities(
    thinking_mode=ThinkingMode.BUDGET,
    supports_sampling_params=True,
    supports_effort_levels=frozenset(),
    default_max_tokens=_LEGACY_MAX_TOKENS,
    default_stream_max_tokens=_LEGACY_8K_STREAM_MAX_TOKENS,
    max_output_tokens=_LEGACY_8K_MAX_OUTPUT_TOKENS,
)
#: The fallback for everything unrecognised: budget thinking, sampling allowed,
#: and the 4096 output floor. Like Haiku 4.5 these predate ``output_config``
#: altogether — an effort tier sizes their ``budget_tokens`` and is never named
#: on the wire — so the effort set is empty.
_LEGACY = ModelCapabilities(
    thinking_mode=ThinkingMode.BUDGET,
    supports_sampling_params=True,
    supports_effort_levels=frozenset(),
    default_max_tokens=_LEGACY_MAX_TOKENS,
    default_stream_max_tokens=_LEGACY_STREAM_MAX_TOKENS,
    max_output_tokens=_LEGACY_MAX_OUTPUT_TOKENS,
)
#: An unlisted ``claude-*`` id that reads as 5.x or 4.7+. Adaptive thinking and
#: no sampling params are safe guesses for that generation; the extended effort
#: tiers are NOT — a family that lacks ``xhigh``/``max`` answers 400 — so an
#: unrecognised id only ever gets the tiers every adaptive family has.
_MODERN_UNLISTED = ModelCapabilities(
    thinking_mode=ThinkingMode.ADAPTIVE,
    supports_sampling_params=False,
    supports_effort_levels=_BASE_EFFORTS,
)

#: Model-id prefix → capabilities. Longest matching prefix wins.
_TABLE: dict[str, ModelCapabilities] = {
    "claude-fable-5-1": _MODERN_NO_FORCED_TOOLS,
    "claude-fable-5": _MODERN,
    "claude-mythos-5-1": _MODERN_NO_FORCED_TOOLS,
    "claude-mythos-5": _MODERN,
    "claude-opus-5": _MODERN,
    "claude-opus-4-8": _MODERN,
    "claude-opus-4-7": _MODERN,
    "claude-opus-4-6": _GEN_4_6,
    "claude-sonnet-5": _MODERN,
    "claude-sonnet-4-6": _GEN_4_6,
    "claude-haiku-4-5": _HAIKU_4_5,
    "claude-3-5-sonnet": _LEGACY_8K,
    "claude-3-5-haiku": _LEGACY_8K,
}

#: ``claude-<family>-<major>[-<minor>]`` in a normalised id, used to classify
#: a Claude model this table does not list yet. The family segment must be
#: alphabetic: the pre-4 scheme put the version first (``claude-3-5-sonnet``),
#: and reading its "3-5" as a family plus a major would date a 2024 model into
#: the modern contract.
_VERSION_RE = re.compile(r"claude-[a-z]+-(\d+)(?:-(\d+))?")


def _normalise(model: str) -> str:
    """Lowercase the id and strip vendor prefixes/suffixes around the family.

    Bedrock and Vertex expose the same models under decorated ids
    (``us.anthropic.claude-opus-5-v1:0``); matching the bare family name keeps
    one table valid for all three serving backends.
    """
    text = (model or "").strip().lower()
    index = text.find("claude-")
    return text[index:] if index > 0 else text


def listed_capabilities_for(model: str) -> ModelCapabilities | None:
    """The table row for *model*, or ``None`` when the table does not list it.

    The distinction matters wherever a *guess* must not be acted on: the
    fallback profile describes the oldest models we know of, and a great many
    current ids land on it (Bedrock and Vertex spellings, ``-latest`` aliases,
    gateway names, anything released after this table was written).

    Args:
        model: Provider model id (any serving backend's spelling).

    Returns:
        ModelCapabilities | None: The row matched by longest prefix, or None.
    """
    normalised = _normalise(model)
    if not normalised:
        return None
    best: str | None = None
    for prefix in _TABLE:
        if normalised.startswith(prefix) and (best is None or len(prefix) > len(best)):
            best = prefix
    return _TABLE[best] if best is not None else None


def capabilities_for(model: str) -> ModelCapabilities:
    """Capabilities for *model*, by longest matching prefix.

    Args:
        model: Provider model id (any serving backend's spelling).

    Returns:
        ModelCapabilities: The matching row; for an unlisted ``claude-*`` id a
        5.x or 4.7+ version reads as a modern family, and everything else
        falls back to the legacy (budget thinking, sampling allowed) profile.
    """
    listed = listed_capabilities_for(model)
    if listed is not None:
        return listed
    return _infer_unlisted(_normalise(model))


def _infer_unlisted(normalised: str) -> ModelCapabilities:
    """Classify a ``claude-*`` id the table does not carry yet.

    A new 5.x model (or a 4.7+ one) follows the modern contract — adaptive
    thinking, no sampling params — so guessing modern keeps a freshly released
    model working instead of sending it a payload that 400s. The guess stops
    at the effort tiers: ``xhigh``/``max`` exist on some of that generation and
    not others, and the listed rows are the only place that is known, so an
    unrecognised id is never sent a tier it may reject. Anything older, or not
    a Claude model at all, stays on the permissive legacy profile.
    """
    if not normalised.startswith("claude-"):
        return _LEGACY
    match = _VERSION_RE.match(normalised)
    if match is None:
        return _LEGACY
    major = int(match.group(1))
    minor = int(match.group(2) or 0)
    if major >= 5 or (major == 4 and minor >= 7):
        return _MODERN_UNLISTED
    return _LEGACY


def supports_sampling_params(model: str) -> bool:
    """Whether ``temperature``/``top_p``/``top_k`` may be sent to *model*."""
    return capabilities_for(model).supports_sampling_params


def rejects_forced_tool_choice(model: str) -> bool:
    """Whether a forced ``tool_choice`` (``any``/``tool``) 400s on *model*."""
    return capabilities_for(model).rejects_forced_tool_choice


def default_max_tokens(model: str, *, streaming: bool = False) -> int:
    """Output cap to send for *model* when the caller specified none.

    Args:
        model: Provider model id.
        streaming: True for a streaming request, which supports a much larger
            cap than a buffered one.

    Returns:
        int: The recommended ``max_tokens`` value.
    """
    caps = capabilities_for(model)
    return caps.default_stream_max_tokens if streaming else caps.default_max_tokens


def configured_max_tokens(explicit: int | None, config: object) -> int | None:
    """The output cap to request: the caller's, else ``LLM_MAX_TOKENS``.

    ``LLMConfig.max_tokens`` was bound from the environment and advertised in
    the templates, yet no call path read it — so setting ``LLM_MAX_TOKENS``
    capped nothing, and providers without a per-family default (OpenAI,
    Gemini, Ollama) generated up to the model's own ceiling on every call.
    ``None`` keeps the provider's default (the family table for Anthropic).

    Args:
        explicit: The caller's ``max_tokens``; always wins when given.
        config: The service's ``LLMConfig`` (anything with ``max_tokens``).

    Returns:
        int | None: The cap to forward, or ``None`` for the provider default.
    """
    if explicit is not None:
        return explicit
    value = getattr(config, "max_tokens", None)
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def clamp_max_tokens(model: str, max_tokens: int) -> int:
    """Bound an output request by a ceiling we actually know *model* has.

    Only listed rows clamp. The fallback profile carries the *oldest* ceiling
    we know of, and applying that to every unrecognised id would silently
    truncate a caller's explicit request on a model that supports eight times
    as much — the guess is fine for choosing a default, never for overriding
    something the caller asked for.

    Args:
        model: Provider model id.
        max_tokens: The requested output cap.

    Returns:
        int: The request, or the family ceiling when the table knows one and
        the request exceeds it.
    """
    caps = listed_capabilities_for(model)
    if caps is None or max_tokens <= caps.max_output_tokens:
        return max_tokens
    logger.debug(
        "model_max_tokens_clamped",
        extra={
            "model": model,
            "requested": max_tokens,
            "ceiling": caps.max_output_tokens,
        },
    )
    return caps.max_output_tokens


def clamp_effort(model: str, effort: str | None) -> str | None:
    """Strongest effort tier *model* supports at or below *effort*.

    ``xhigh`` and ``max`` exist only on the newest families; sending one to an
    older model is a 400, so the request is degraded to ``high`` rather than
    failed.

    Args:
        model: Provider model id.
        effort: Requested tier, or ``None``.

    Returns:
        str | None: The tier to send, or ``None`` when the input is empty or
        not a known tier.
    """
    if not effort:
        return None
    wanted = effort.strip().lower()
    if wanted not in EFFORT_LADDER:
        return None
    supported = capabilities_for(model).supports_effort_levels
    for tier in EFFORT_LADDER[EFFORT_LADDER.index(wanted) :]:
        if tier in supported:
            return tier
    return None
