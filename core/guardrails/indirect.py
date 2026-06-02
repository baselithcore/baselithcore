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
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum

from core.observability.logging import get_logger

logger = get_logger(__name__)

# Opt-in escalation: when set truthy, ``scan_external_content`` does not just
# log findings but returns a sanitized copy (invisibles/bidi/HTML comments
# stripped). Defaults off so wiring the scanner into ingestion paths is purely
# additive and cannot change what reaches the model.
_SANITIZE_ENV = "BASELITH_SANITIZE_EXTERNAL_CONTENT"


class IndirectFindingKind(str, Enum):
    """Category of a single indirect-injection finding."""

    ZERO_WIDTH = "zero_width"
    BIDI_OVERRIDE = "bidi_override"
    HTML_COMMENT = "html_comment"
    HIDDEN_CSS = "hidden_css"
    AI_DIRECTIVE = "ai_directive"


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
    r"system\s+prompt",
    r"\bAI\b[^.\n]{0,40}\b(must|should|forward|send|execute|run)\b",
    r"forward\s+.{0,40}\bto\s+\S+@\S+",
    r"\bsend_email\b|\bexec\b|\bsubprocess\b",
]
_COMPILED_AI_DIRECTIVES = [re.compile(p, re.IGNORECASE) for p in _AI_DIRECTIVE_PATTERNS]


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

        normalized = self._strip_invisibles(content)
        for pattern in _COMPILED_AI_DIRECTIVES:
            match = pattern.search(normalized)
            if match:
                findings.append(
                    IndirectFinding(
                        IndirectFindingKind.AI_DIRECTIVE,
                        f"agent-directed phrase: {match.group(0)[:80]!r}",
                    )
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

        Strips zero-width / bidi characters and removes HTML comments so the
        remaining visible text is what a human reader would actually see.
        Does not rewrite visible directive phrases — blocking those is a
        caller policy decision based on ``scan``.
        """
        cleaned = self._strip_invisibles(content)
        cleaned = _HTML_COMMENT_RE.sub("", cleaned)
        return cleaned

    @staticmethod
    def _strip_invisibles(content: str) -> str:
        """Remove zero-width and bidi-override characters."""
        cleaned = _ZERO_WIDTH_RE.sub("", content)
        return _BIDI_RE.sub("", cleaned)

    @staticmethod
    def _looks_like_directive(text: str) -> bool:
        """Heuristic: does a comment body read as an agent instruction?"""
        normalized = unicodedata.normalize("NFKC", text)
        return any(p.search(normalized) for p in _COMPILED_AI_DIRECTIVES)


# Shared stateless scanner; reused across ingestion boundaries to avoid
# recompiling the (already module-level) patterns per call.
_DEFAULT_SCANNER = IndirectInjectionScanner()


def _sanitize_enabled() -> bool:
    """Whether env requests sanitizing (not just logging) flagged content."""
    return os.environ.get(_SANITIZE_ENV, "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
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
