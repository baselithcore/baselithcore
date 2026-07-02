"""Deterministic truncation of tool/observation output before it re-enters context.

Large tool results (file dumps, HTTP bodies, query rows) bloat the context
window and can overflow it. This keeps a head and a tail — the two regions that
usually carry the signal (the start of the payload and the final status/error) —
and replaces the middle with a marker recording how much was dropped. The cut is
deterministic (no sampling) so replayed trajectories stay stable.
"""

import os

__all__ = ["truncate_tool_output", "DEFAULT_TOOL_OUTPUT_MAX_CHARS"]


def _default_max_chars() -> int:
    raw = os.getenv("BASELITH_TOOL_OUTPUT_MAX_CHARS", "8000")
    try:
        return int(raw)
    except ValueError:
        return 8000


# Resolved once at import; override per-call via ``max_chars`` if needed.
DEFAULT_TOOL_OUTPUT_MAX_CHARS = _default_max_chars()


def truncate_tool_output(text: str, max_chars: int | None = None) -> str:
    """Truncate ``text`` to roughly ``max_chars``, keeping head and tail.

    Args:
        text: The rendered tool output / observation.
        max_chars: Character budget. ``None`` uses the env-configured default;
            ``<= 0`` disables truncation.

    Returns:
        Either ``text`` unchanged (already within budget or truncation disabled)
        or ``head + marker + tail`` where the marker names the dropped char count.
    """
    limit = DEFAULT_TOOL_OUTPUT_MAX_CHARS if max_chars is None else max_chars
    if limit <= 0 or len(text) <= limit:
        return text

    # Head-heavy 2:1 split; the head usually frames the payload, the tail
    # carries the trailing status/error line. Guarantee at least 1 char of tail.
    head_len = max(1, (limit * 2) // 3)
    tail_len = max(1, limit - head_len)
    omitted = len(text) - head_len - tail_len
    if omitted <= 0:
        return text
    marker = f"\n… [truncated {omitted} chars] …\n"
    return text[:head_len] + marker + text[-tail_len:]
