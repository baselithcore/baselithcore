"""Phrases that address an AI agent from inside content the agent reads.

An indirect injection rarely says "ignore previous instructions" any more. It
talks to the agent the way an operator would: a note *for the AI*, a step to
complete *before answering the user*, a request to keep something *from the
user*, a fake closing tag that pretends the tool result is over, an encoded
block to *decode and carry out*. A human reader is never the audience of those
phrases, which is what makes them a signal inside fetched content.

The families, each anchored to the agent or to the user it serves so that
ordinary business text does not trip them:

* **addressing** - a note, message or instruction *for the AI / LLM*;
* **precedence** - a step the agent *must* take before it answers the user;
* **concealment** - keep the act from the user, or text the user cannot see;
* **role markup** - closing ``tool_result`` / opening ``system`` or
  ``assistant_instructions`` tags, ChatML tokens;
* **encoded lure** - decode a base64 / hex block and follow it;
* **silent execution** - perform it silently;
* **carry-over** - append something to the agent's next outgoing message.

English and Italian carry these families; the native-script languages of
:mod:`baselith.core.guardrails.multilingual` add their override families
("ignore the previous instructions" and kin). The romanised families
(``*-latn``) are left to ``InputGuard``: they exist for people typing in the
Latin alphabet, cost the most, and are the likeliest to fire on arbitrary
fetched text. Every pattern is linear: bounded gaps, no nested quantifiers.
Benign sentences that must stay clean are pinned in
``tests/unit/core/guardrails/test_indirect_agent_directives.py``.

Cost: the scanner runs on every tool result, so the agent families are one
alternation over the lowercased text (one pass), and the override families
read only the first :data:`MAX_OVERRIDE_SCAN_CHARS` characters, the budget the
decode stage uses too.
"""

from __future__ import annotations

import re
import unicodedata

from .multilingual import COMPILED_MULTILINGUAL_PATTERNS

_AI = r"(?:the\s+|an?\s+|any\s+)?(?:ai|llm|language\s+model|chatbot)"
_USER_EN = r"(?:the\s+)?user"

AGENT_DIRECTIVE_PATTERNS: dict[str, tuple[str, ...]] = {
    "en": (
        rf"\b(?:note|message|instructions?|directive|memo)\s+(?:for|to)\s+{_AI}\b",
        r"\b(?:ai|llm)\s+(?:agent|assistant|model)\s+(?:instructions?|directive|note|command)\b",
        rf"\bbefore\s+(?:you\s+)?(?:answer|reply|respond)(?:ing)?\s+(?:to\s+)?{_USER_EN}\b"
        r"[^.\n]{0,4}\byou\s+(?:must|need\s+to|have\s+to|should|are\s+required\s+to)\b",
        rf"\bdo\s+not\s+(?:tell|mention|inform|notify|alert)\b[^.\n]{{0,30}}\b{_USER_EN}\b",
        rf"\bwithout\s+(?:telling|informing|notifying|alerting)\s+{_USER_EN}\b",
        rf"\b{_USER_EN}\s+(?:cannot|can['’]?t|will\s+not|won['’]t)\s+see\s+(?:this|it)\b",
        r"<\s*/?\s*(?:tool_result|tool_output|tool_response|function_results?"
        r"|assistant_instructions?|system_(?:prompt|instructions?|message))\s*>",
        r"<\|im_(?:start|end)\|>",
        r"\bdecode\s+(?:it|this|that|the\s+(?:block|text|string|message|following))\b"
        r"[^.\n]{0,40}\b(?:base64|hex|rot13)\b[^.\n]{0,40}"
        r"\b(?:follow|execute|obey|perform|carry\s+(?:it\s+)?out)\b",
        r"\b(?:perform|execute|complete|do)\s+(?:it|this|that|them)\s+silently\b",
        r"\bsilently\s+(?:perform|execute|carry\s+out|complete)\b",
        r"\b(?:append|insert)\b[^.\n]{0,60}\bto\s+your\s+next\s+"
        r"(?:outgoing\s+)?(?:message|reply|response|email|answer)\b",
    ),
    "it": (
        r"\b(?:nota|messaggio|istruzion[ei])\s+per\s+(?:l['’]\s*)?(?:ia|ai|llm|assistente\s+ia)\b",
        r"\bprima\s+di\s+rispondere\s+all['’]\s*utente\b[^.\n]{0,10}"
        r"\b(?:devi|occorre|è\s+necessario)\b",
        r"\bnon\s+(?:dirlo|dire|menzionarlo|menzionare|segnalarlo|segnalare)\b"
        r"[^.\n]{0,30}\butente\b",
        r"\bsenza\s+(?:dirlo|avvisare|informare)\s+(?:all['’]\s*|l['’]\s*)?utente\b",
        r"\bdecodifica(?:lo|la|\s+(?:questo|questa|il\s+blocco|il\s+testo))\b"
        r"[^.\n]{0,40}\b(?:base64|esadecimale|hex)\b[^.\n]{0,40}\b(?:esegui|segui|applica)\b",
        r"\b(?:eseguilo|eseguila|fallo)\s+in\s+silenzio\b",
    ),
}

COMPILED_AGENT_DIRECTIVES: list[tuple[str, re.Pattern[str]]] = [
    (lang, re.compile(source, re.IGNORECASE))
    for lang, sources in AGENT_DIRECTIVE_PATTERNS.items()
    for source in sources
]

#: Every agent family in one alternation, matched against the lowercased text.
_AGENT_ALTERNATION = re.compile(
    "|".join(
        f"(?:{source.lower()})"
        for sources in AGENT_DIRECTIVE_PATTERNS.values()
        for source in sources
    )
)

#: The override families of the native-script languages (see module doc).
_OVERRIDE_PATTERNS: list[re.Pattern[str]] = [
    pattern
    for lang, pattern in COMPILED_MULTILINGUAL_PATTERNS
    if not lang.endswith("-latn")
]

#: Findings reported per scan at most: one phrase proves the point, and a
#: page full of them should not flood the log.
MAX_MATCHES = 5
#: Characters the override families read: the decode stage's budget.
MAX_OVERRIDE_SCAN_CHARS = 200_000


def agent_directive_matches(text: str) -> list[str]:
    """Snippets of ``text`` that address an AI agent, at most :data:`MAX_MATCHES`.

    Args:
        text: Content with invisible characters already stripped.

    Returns:
        The matched phrases, in pattern order, each at most 80 characters.
    """
    if not text:
        return []
    lowered = unicodedata.normalize("NFKC", text).lower()
    found: list[str] = []

    def keep(phrase: str) -> bool:
        snippet = phrase[:80]
        if snippet not in found:
            found.append(snippet)
        return len(found) >= MAX_MATCHES

    for match in _AGENT_ALTERNATION.finditer(lowered):
        if keep(match.group(0)):
            return found
    head = lowered[:MAX_OVERRIDE_SCAN_CHARS]
    for pattern in _OVERRIDE_PATTERNS:
        hit = pattern.search(head)
        if hit is not None and keep(hit.group(0)):
            return found
    return found


__all__ = [
    "AGENT_DIRECTIVE_PATTERNS",
    "COMPILED_AGENT_DIRECTIVES",
    "MAX_MATCHES",
    "MAX_OVERRIDE_SCAN_CHARS",
    "agent_directive_matches",
]
