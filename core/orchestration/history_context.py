"""Conversation history as untrusted prompt data.

Earlier turns are replayed into the prompt so a follow-up ("and the second
one?") can resolve against what was already said. Those turns are not
operator text: an assistant turn may quote a tool result or a retrieved
document, and a user turn is whatever the client sent. Inserted verbatim they
read exactly like the prompt around them, so a poisoned turn could keep
issuing instructions on every later request of the conversation.

:func:`render_history_context` gives history the treatment recalled memories
and retrieved chunks already get: one injection scan and one untrusted
envelope, with the ``User:`` / ``Assistant:`` role labels left readable
inside it.
"""

from __future__ import annotations

__all__ = ["CONVERSATION_HISTORY_SOURCE", "render_history_context"]

#: Envelope ``tool`` attribute (and scan ``source``) for replayed history.
CONVERSATION_HISTORY_SOURCE = "conversation_history"


def render_history_context(history: str) -> str:
    """Render prior conversation turns as one scanned, enveloped block.

    The text is scanned for indirect prompt injection with
    :func:`~core.guardrails.indirect.scan_external_content` (findings logged
    under ``conversation_history``; flagged content sanitized under the
    ``BASELITH_SANITIZE_EXTERNAL_CONTENT`` policy, log-only when it is off),
    then sealed in a single envelope by
    :func:`~core.orchestration.tool_output.wrap_untrusted`, which escapes any
    envelope marker inside the turns so a quoted closing tag cannot break out.

    Call it exactly once per prompt, at the point the history is rendered:
    :func:`~core.orchestration.tool_output.wrap_untrusted` deliberately has no
    idempotency shortcut, so a second pass would nest the envelope.

    Args:
        history: The formatted turns (``User: …`` / ``Assistant: …``), oldest
            first.

    Returns:
        The enveloped history, or ``""`` when there is none (so a consumer's
        "no history" check keeps working).
    """
    if not history or not history.strip():
        return ""
    from core.guardrails.indirect import scan_external_content
    from core.orchestration.tool_output import wrap_untrusted

    scanned = scan_external_content(history.strip(), source=CONVERSATION_HISTORY_SOURCE)
    return wrap_untrusted(scanned, source=CONVERSATION_HISTORY_SOURCE)
