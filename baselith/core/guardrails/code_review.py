"""Deterministic code security review — regex + entropy, no LLM.

Scans generated code or diffs for leaked credentials and dangerous call
patterns before they ship. The scan is a line-oriented, purely deterministic
pass: every regex is precompiled at module level, no network calls, no model
inference, so a verdict is reproducible byte-for-byte.

Scope and deliberate limitations (documented, not bugs):

- **Comments and strings are scanned too.** The reviewer does not parse the
  language; a credential pasted into a comment is still a leak, so flagging
  it is the intended behavior. The flip side is that prose mentioning e.g.
  ``os.system(`` verbatim will also be flagged — reviewers see comments.
- **Checks are line-based.** ``yaml.load(`` spread across multiple lines is
  judged on the line holding the call opener; a ``Loader=`` kwarg on a later
  line is not seen. Same for ``shell=True`` — any occurrence flags the line
  without proving the enclosing call is ``subprocess``.

Severity model:

- Secrets (AWS/GitHub/OpenAI/Slack keys, private key blocks, high-entropy
  literals assigned to secret-like identifiers) are always ``high``.
- ``eval(`` / ``exec(`` on non-literal input is ``high`` (arbitrary code
  execution); the remaining dangerous patterns are ``medium``.

Example:
    >>> review = review_code('key = "AKIA' + 'IOSFODNN7EXAMPLE"')
    >>> review.verdict
    'flagged'
    >>> review.severity
    'high'
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

Severity = Literal["low", "medium", "high"]

_SEVERITY_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}

#: Minimum literal length considered by the entropy check.
_ENTROPY_MIN_LENGTH = 20
#: Shannon entropy (bits/char) at or above which a literal is deemed random.
_ENTROPY_THRESHOLD = 4.0

# --- Secret patterns (always severity high) --------------------------------

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "AWS access key ID detected",
    ),
    (
        re.compile(r"\b(?:ghp|gho)_[A-Za-z0-9]{20,}\b"),
        "GitHub token detected",
    ),
    (
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
        "GitHub fine-grained token detected",
    ),
    (
        re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
        "OpenAI-style API key detected",
    ),
    (
        re.compile(r"\bxox[abps]-[A-Za-z0-9-]{8,}\b"),
        "Slack token detected",
    ),
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        "Private key block detected",
    ),
)

#: ``secret``-like identifier assigned a quoted literal of >= 20 chars.
#: Tolerates a type annotation (``token: str = "..."``) and dict-key form
#: (``"api_key": "..."``). f-strings do not match (the ``f`` prefix breaks
#: the quote adjacency), which is correct: interpolated values are not
#: literal credentials.
_ENTROPY_ASSIGNMENT_RE = re.compile(
    r"(?i)[\"']?\b\w*(?:secret|token|password|api_?key)\w*\b[\"']?"
    r"\s*(?::\s*[A-Za-z_.\[\], ]{0,40})?"
    r"\s*[=:]\s*"
    r"(?P<quote>[\"'])(?P<value>[^\"'\n]{20,})(?P=quote)"
)

# --- Dangerous patterns ----------------------------------------------------

#: ``eval(`` / ``exec(`` as a bare call (dotted names like ``model.eval()``
#: excluded) capturing the first non-space character of the argument.
_EVAL_EXEC_RE = re.compile(r"(?<![\w.])(?P<func>eval|exec)\s*\(\s*(?P<head>[^)\s]?)")

_YAML_LOAD_RE = re.compile(r"\byaml\.load\s*\(")
_YAML_LOADER_KWARG_RE = re.compile(r"\bLoader\s*=")

_DANGEROUS_PATTERNS: tuple[tuple[re.Pattern[str], str, Severity], ...] = (
    (
        re.compile(r"\bshell\s*=\s*True\b"),
        "subprocess call with shell=True enables command injection",
        "medium",
    ),
    (
        re.compile(r"\bpickle\.loads\s*\("),
        "pickle.loads deserializes arbitrary objects (code execution risk)",
        "medium",
    ),
    (
        re.compile(r"\bverify\s*=\s*False\b"),
        "TLS certificate verification disabled (verify=False)",
        "medium",
    ),
    (
        re.compile(r"\bos\.system\s*\("),
        "os.system spawns a shell (command injection risk); use subprocess",
        "medium",
    ),
)


@dataclass(frozen=True)
class CodeReviewComment:
    """One finding produced by the deterministic reviewer.

    Attributes:
        severity: ``low`` | ``medium`` | ``high``.
        message: Human-readable description of the finding.
        line: 1-based line number when determinable, else ``None``.
    """

    severity: Severity
    message: str
    line: int | None = None


@dataclass
class CodeReview:
    """Aggregate verdict over one piece of code.

    Attributes:
        verdict: ``flagged`` when any comment exists, else ``approved``.
        severity: Highest comment severity; ``"none"`` when approved.
        comments: Individual findings, in line order.
    """

    verdict: Literal["approved", "flagged"]
    severity: str
    comments: list[CodeReviewComment] = field(default_factory=list)


def _shannon_entropy(value: str) -> float:
    """Shannon entropy of ``value`` in bits per character."""
    if not value:
        return 0.0
    total = len(value)
    return -sum(
        (count / total) * math.log2(count / total) for count in Counter(value).values()
    )


def _scan_secrets(line: str, lineno: int) -> list[CodeReviewComment]:
    """Secret findings for one line (specific patterns, then entropy)."""
    found = [
        CodeReviewComment(severity="high", message=message, line=lineno)
        for pattern, message in _SECRET_PATTERNS
        if pattern.search(line)
    ]
    if found:
        # A specific pattern already covers this line; the entropy check
        # would only duplicate the finding.
        return found
    match = _ENTROPY_ASSIGNMENT_RE.search(line)
    if match:
        value = match.group("value")
        if (
            len(value) >= _ENTROPY_MIN_LENGTH
            and _shannon_entropy(value) >= _ENTROPY_THRESHOLD
        ):
            found.append(
                CodeReviewComment(
                    severity="high",
                    message=(
                        "High-entropy literal assigned to a secret-like "
                        "identifier (possible hardcoded credential)"
                    ),
                    line=lineno,
                )
            )
    return found


def _scan_dangerous(line: str, lineno: int) -> list[CodeReviewComment]:
    """Dangerous-pattern findings for one line."""
    found: list[CodeReviewComment] = []
    match = _EVAL_EXEC_RE.search(line)
    if match:
        head = match.group("head")
        # A quote or digit right after the paren means a literal argument;
        # empty head means a no-arg call. Everything else is dynamic input.
        if head and head not in {'"', "'"} and not head.isdigit():
            found.append(
                CodeReviewComment(
                    severity="high",
                    message=(
                        f"{match.group('func')}() on non-literal input "
                        "executes arbitrary code"
                    ),
                    line=lineno,
                )
            )
    if _YAML_LOAD_RE.search(line) and not _YAML_LOADER_KWARG_RE.search(line):
        found.append(
            CodeReviewComment(
                severity="medium",
                message=(
                    "yaml.load without an explicit Loader can construct "
                    "arbitrary Python objects; use yaml.safe_load"
                ),
                line=lineno,
            )
        )
    for pattern, message, severity in _DANGEROUS_PATTERNS:
        if pattern.search(line):
            found.append(
                CodeReviewComment(severity=severity, message=message, line=lineno)
            )
    return found


def review_code(text: str, *, filename: str | None = None) -> CodeReview:
    """Review code or a diff for secrets and dangerous patterns.

    Purely deterministic: line-oriented regex matching plus a Shannon-entropy
    check for credential-like literals. Comments and docstrings are scanned
    like any other line (see module docstring for the rationale).

    Args:
        text: Source code or diff text to scan.
        filename: Optional origin filename, recorded for caller context only —
            it does not change which checks run.

    Returns:
        A :class:`CodeReview` whose verdict is ``flagged`` when at least one
        finding exists, with ``severity`` set to the highest finding severity
        (``"none"`` when approved).
    """
    del filename  # Advisory context only; scanning is content-based.
    comments: list[CodeReviewComment] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        comments.extend(_scan_secrets(line, lineno))
        comments.extend(_scan_dangerous(line, lineno))
    if not comments:
        return CodeReview(verdict="approved", severity="none", comments=[])
    worst = max(comments, key=lambda c: _SEVERITY_RANK[c.severity]).severity
    return CodeReview(verdict="flagged", severity=worst, comments=comments)


__all__ = [
    "CodeReview",
    "CodeReviewComment",
    "Severity",
    "review_code",
]
