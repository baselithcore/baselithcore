"""
Indirect prompt-injection scanning for externally-fetched content.

Direct input guards (``InputGuard``) inspect what the *user* typed. They do
not see instructions smuggled inside content the agent fetches itself — web
pages, emails, documents, tool output. Indirect injection hides agent
directives in that data: zero-width unicode, HTML comments, CSS-invisible
text, or explicit AI-command phrases.

This module scans any blob of *untrusted external content* before it enters
the model's context window. It is detection-first and additive: callers run
``IndirectInjectionScanner.scan(...)`` on fetched content and decide whether
to block, redact (``sanitize``), or flag for review.

Agent-directed phrasing beyond the classic override ("ignore previous
instructions") - notes for the AI, steps to take before answering the user,
concealment from the user, fake role markup, decode-and-follow lures, in
English, Italian and the override families of every language the guards
carry - comes from :mod:`baselith.core.guardrails.agent_directives`.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum

from baselith.core.observability.logging import get_logger

from .agent_directives import agent_directive_matches

logger = get_logger(__name__)

# Sanitizing switch: when enabled (the default), ``scan_external_content``
# does not just log findings but returns a sanitized copy (invisibles/bidi/
# HTML comments stripped) of flagged content. Set to 0/false/no/off for the
# legacy detection-only mode where content always passes through unchanged.
_SANITIZE_ENV = "BASELITH_SANITIZE_EXTERNAL_CONTENT"


class IndirectFindingKind(str, Enum):
    """Category of a single indirect-injection finding."""

    ZERO_WIDTH = "zero_width"
    BIDI_OVERRIDE = "bidi_override"
    HTML_COMMENT = "html_comment"
    HIDDEN_CSS = "hidden_css"
    AI_DIRECTIVE = "ai_directive"
    EXFIL_LINK = "exfil_link"


@dataclass(frozen=True)
class IndirectFinding:
    """A single suspicious construct discovered in external content."""

    kind: IndirectFindingKind
    detail: str


@dataclass
class IndirectScanResult:
    """Outcome of scanning one blob of external content."""

    is_suspicious: bool
    findings: list[IndirectFinding] = field(default_factory=list)

    def kinds(self) -> set[IndirectFindingKind]:
        """Return the distinct finding kinds present in the result."""
        return {f.kind for f in self.findings}


# Zero-width / invisible formatting characters commonly used to hide text.
# Built from explicit codepoints so the source stays pure-ASCII and the bytes
# survive editors and linters:
#   U+200B ZWSP, U+200C ZWNJ, U+200D ZWJ, U+2060 WORD JOINER, U+FEFF BOM.
_ZERO_WIDTH_CHARS = "".join(chr(cp) for cp in (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF))

# Bidirectional override / isolate characters (text-direction spoofing):
#   U+202A..U+202E (LRE RLE PDF LRO RLO), U+2066..U+2069 (LRI RLI FSI PDI).
_BIDI_CHARS = "".join(
    chr(cp) for cp in (*range(0x202A, 0x202F), *range(0x2066, 0x206A))
)

# Unicode Tags block, U+E0000..U+E007F: each tag character mirrors one ASCII
# character (``chr(0xE0000 + ord(c))``), renders as nothing in a browser or an
# editor, and is read by many models ("ASCII smuggling"). The variation
# selectors supplement, U+E0100..U+E01EF, is the second invisible carrier.
# Written as escapes so the source stays pure ASCII.
_TAG_CLASS = "[\\U000E0000-\\U000E007F\\U000E0100-\\U000E01EF]"
# A subdivision flag (England, Scotland, Wales) is legitimate content that
# really is a black flag followed by tag characters and a cancel tag.
_FLAG_SEQUENCE = "\\U0001F3F4[\\U000E0061-\\U000E007A]{2,7}\\U000E007F"
_TAG_CHARS_RE = re.compile(_TAG_CLASS)
_FLAG_OR_TAG_RE = re.compile(f"({_FLAG_SEQUENCE})|{_TAG_CLASS}")
_TAG_BASE = 0xE0000
_TAG_PRINTABLE = range(_TAG_BASE + 0x20, _TAG_BASE + 0x7F)

_ZERO_WIDTH_RE = re.compile(f"[{_ZERO_WIDTH_CHARS}]")
_BIDI_RE = re.compile(f"[{_BIDI_CHARS}]")
_HTML_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)

# CSS rules that visually hide text while leaving it in the DOM/source.
_HIDDEN_CSS_RE = re.compile(
    r"display\s*:\s*none"
    r"|visibility\s*:\s*hidden"
    r"|font-size\s*:\s*0(?:px|pt|em|rem)?\b"
    r"|opacity\s*:\s*0(?:\.0+)?\b"
    r"|color\s*:\s*(?:#fff(?:fff)?\b|white\b)"
    r"|text-indent\s*:\s*-\d",
    re.IGNORECASE,
)

# Agent-directed instruction phrases. These target the *agent*, not a human
# reader, so their presence inside fetched content is a strong signal.
_AI_DIRECTIVE_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?",
    r"disregard\s+(all\s+)?(previous|prior|above)",
    r"forget\s+(everything|all|your)\s+(instructions?|training)",
    r"new\s+(system\s+)?instructions?\s*:",
    r"you\s+are\s+now\s+(a|an|the)",
    # Directive forms only: a page that merely talks about system prompts is
    # ordinary documentation, not an instruction to the agent.
    r"(override|replace|update|ignore|reveal|print)\s+(the\s+|your\s+)?system\s+prompt",
    r"system\s+prompt\s*(override|update)?\s*:",
    r"\bAI\b[^.\n]{0,40}\b(must|should|forward|send|execute|run)\b",
    r"forward\s+.{0,40}\bto\s+\S+@\S+",
    r"\bsend_email\b|\bexec\b|\bsubprocess\b",
]
_COMPILED_AI_DIRECTIVES = [re.compile(p, re.IGNORECASE) for p in _AI_DIRECTIVE_PATTERNS]

# Zero-click exfiltration channels. A markdown *image* whose URL carries a
# query string is fetched by any renderer without a click, so it is flagged
# unconditionally; a plain link is flagged only when the query names a
# sensitive value (secret=, token=, conversation=, ...), because ordinary web
# pages are full of links with harmless query strings.
_EXFIL_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*https?://[^)\s]+\?[^)\s]*\)")
_EXFIL_LINK_RE = re.compile(
    r"(?<!!)\[[^\]]*\]\(\s*https?://[^)\s]*\?[^)\s]*"
    r"\b(secret|token|api[_-]?key|password|conversation|history|session|data)="
    r"[^)\s]*\)",
    re.IGNORECASE,
)


class IndirectInjectionScanner:
    """
    Scan untrusted external content for hidden agent directives.

    The scanner is stateless and cheap (pure regex + unicode inspection),
    designed to run on every web page, email, or document the agent ingests
    before that content reaches the model.
    """

    def scan(self, content: str) -> IndirectScanResult:
        """
        Inspect ``content`` and return a structured result.

        Args:
            content: Raw external content (HTML, email body, document text).

        Returns:
            IndirectScanResult: suspicious flag plus itemized findings.
        """
        if not content:
            return IndirectScanResult(is_suspicious=False)

        findings: list[IndirectFinding] = []

        zw = _ZERO_WIDTH_RE.findall(content)
        if zw:
            findings.append(
                IndirectFinding(
                    IndirectFindingKind.ZERO_WIDTH,
                    f"{len(zw)} zero-width/invisible character(s)",
                )
            )
        smuggled = _FLAG_OR_TAG_RE.sub(
            lambda m: "" if m.group(1) else m.group(0), content
        )
        hidden = self._decode_tag_text(smuggled)
        tags = _TAG_CHARS_RE.findall(smuggled)
        if tags:
            findings.append(
                IndirectFinding(
                    IndirectFindingKind.ZERO_WIDTH,
                    f"{len(tags)} invisible Unicode tag character(s)",
                )
            )

        if _BIDI_RE.search(content):
            findings.append(
                IndirectFinding(
                    IndirectFindingKind.BIDI_OVERRIDE,
                    "bidirectional text-direction override character(s)",
                )
            )

        for comment in _HTML_COMMENT_RE.findall(content):
            if self._looks_like_directive(comment):
                findings.append(
                    IndirectFinding(
                        IndirectFindingKind.HTML_COMMENT,
                        f"instruction-like HTML comment: {comment.strip()[:80]!r}",
                    )
                )

        if _HIDDEN_CSS_RE.search(content):
            findings.append(
                IndirectFinding(
                    IndirectFindingKind.HIDDEN_CSS,
                    "CSS that visually hides text while keeping it in source",
                )
            )

        exfil = _EXFIL_IMAGE_RE.search(content) or _EXFIL_LINK_RE.search(content)
        if exfil:
            findings.append(
                IndirectFinding(
                    IndirectFindingKind.EXFIL_LINK,
                    f"markdown link/image that exfiltrates data: {exfil.group(0)[:80]!r}",
                )
            )
        normalized = self._strip_invisibles(content)
        if hidden:  # what the smuggled text says is scanned like visible text
            normalized = f"{normalized}\n{hidden}"
        phrases: list[str] = []
        for pattern in _COMPILED_AI_DIRECTIVES:
            match = pattern.search(normalized)
            if match:
                phrases.append(match.group(0)[:80])
        for phrase in agent_directive_matches(normalized):
            if phrase not in phrases:
                phrases.append(phrase)
        findings.extend(
            IndirectFinding(
                IndirectFindingKind.AI_DIRECTIVE, f"agent-directed phrase: {phrase!r}"
            )
            for phrase in phrases
        )

        if findings:
            logger.warning(
                "Indirect injection scan flagged content: %s",
                [f.kind.value for f in findings],
            )

        return IndirectScanResult(
            is_suspicious=bool(findings),
            findings=findings,
        )

    def sanitize(self, content: str) -> str:
        """
        Return a neutralized copy of ``content``.

        Strips zero-width / bidi / Unicode tag characters and removes HTML
        comments so the
        remaining visible text is what a human reader would actually see.
        Does not rewrite visible directive phrases — blocking those is a
        caller policy decision based on ``scan``.
        """
        cleaned = self._strip_invisibles(content)
        cleaned = _HTML_COMMENT_RE.sub("", cleaned)
        return cleaned

    @staticmethod
    def _strip_invisibles(content: str) -> str:
        """Remove zero-width, bidi-override and Unicode tag characters."""
        cleaned = _ZERO_WIDTH_RE.sub("", content)
        cleaned = _FLAG_OR_TAG_RE.sub(lambda m: m.group(1) or "", cleaned)
        return _BIDI_RE.sub("", cleaned)

    @staticmethod
    def _decode_tag_text(content: str) -> str:
        """The ASCII text a run of Unicode tag characters spells, if any."""
        return "".join(
            chr(ord(ch) - _TAG_BASE) for ch in content if ord(ch) in _TAG_PRINTABLE
        )

    @staticmethod
    def _looks_like_directive(text: str) -> bool:
        """Heuristic: does a comment body read as an agent instruction?"""
        normalized = unicodedata.normalize("NFKC", text)
        return any(p.search(normalized) for p in _COMPILED_AI_DIRECTIVES) or bool(
            agent_directive_matches(normalized)
        )


# Shared stateless scanner; reused across ingestion boundaries to avoid
# recompiling the (already module-level) patterns per call.
_DEFAULT_SCANNER = IndirectInjectionScanner()


def _sanitize_enabled() -> bool:
    """Whether flagged content gets sanitized (not just logged).

    ON by default (OWASP ASI-aligned): flagged external content is stripped of
    invisibles/bidi/instruction-bearing comments before reaching the model.
    Set the env flag to ``0/false/no/off`` for legacy detection-only mode.
    """
    return os.environ.get(_SANITIZE_ENV, "true").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def scan_external_content(
    content: str,
    *,
    source: str,
    sanitize: bool | None = None,
) -> str:
    """Scan untrusted external content at an ingestion boundary.

    Detection-first and additive: by default the original ``content`` is
    returned unchanged and any findings are logged with ``source`` for triage.
    Pass ``sanitize=True`` (or set ``BASELITH_SANITIZE_EXTERNAL_CONTENT``) to
    additionally strip invisible/bidi characters and instruction-bearing HTML
    comments before the content reaches the model.

    This is the recommended entry point for callers that fetch content the
    agent did not author — external MCP tool results, scraped pages, documents.

    Args:
        content: Raw external content (tool output, HTML, document text).
        source: Human-readable origin label for logs (tool name, URL).
        sanitize: Force sanitizing on/off. ``None`` defers to the env flag.

    Returns:
        The original content, or a sanitized copy when sanitizing is enabled
        and the content was flagged as suspicious.
    """
    if not content:
        return content

    result = _DEFAULT_SCANNER.scan(content)
    if result.is_suspicious:
        logger.warning(
            "indirect_injection_flagged source=%s kinds=%s",
            source,
            [f.kind.value for f in result.findings],
        )

    do_sanitize = _sanitize_enabled() if sanitize is None else sanitize
    if do_sanitize and result.is_suspicious:
        return _DEFAULT_SCANNER.sanitize(content)
    return content
