"""Failure fingerprinting for engineered loops.

A retry loop that only counts *how many* times it failed cannot tell the
difference between an agent that is converging and one that keeps producing
new code with the same broken result. Hashing the failure signal — not the
attempt — makes that distinction machine-checkable: an unchanged fingerprint
across attempts means the loop is no longer making progress.

The hash must be stable across runs, so every volatile token that a rerun
would change (memory addresses, temp paths, durations, timestamps, PIDs)
is normalized away before hashing.
"""

from __future__ import annotations

import hashlib
import re

__all__ = ["FAILURE_MARKERS", "failure_fingerprint", "failure_lines"]

#: Substrings that mark a line as carrying failure signal. Matched
#: case-insensitively against each line of the evidence.
FAILURE_MARKERS: tuple[str, ...] = (
    "FAILED",
    "ERROR",
    "Exception",
    "Traceback",
    "assert",
    "E   ",
)

# Volatile tokens: two reruns of the same failure differ on these, so they
# would defeat the whole point of the fingerprint if left in.
_VOLATILE: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"0x[0-9a-fA-F]+"), "0xADDR"),
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?"), "TS"),
    (re.compile(r"\b\d+(?:\.\d+)?\s?(?:ms|s|seconds|sec)\b"), "DUR"),
    # B108 false positive: this is a pattern that *erases* temp paths from
    # the evidence before hashing, not a filesystem location this module
    # writes to. Nothing here opens a file.
    (re.compile(r"/tmp/[\w./-]+"), "TMPPATH"),  # nosec B108  # noqa: S108
    (re.compile(r"\b(?:pid|PID)[= ]\d+"), "PID"),
    (re.compile(r"\bin \d+\.\d+s\b"), "DUR"),
    (re.compile(r"\b[0-9a-fA-F]{32,}\b"), "HEX"),
)


def _normalize(line: str) -> str:
    """Strip volatile tokens and collapse whitespace in a single line."""
    text = line.strip()
    for pattern, placeholder in _VOLATILE:
        text = pattern.sub(placeholder, text)
    return re.sub(r"\s+", " ", text)


def failure_lines(evidence: str, *, max_lines: int = 40) -> list[str]:
    """Extract the normalized failure-carrying lines from *evidence*.

    Args:
        evidence: Raw verifier output (test log, linter output, traceback).
        max_lines: Cap on the number of retained lines. Keeps a runaway log
            from making the fingerprint depend on output truncation.

    Returns:
        Normalized failure lines, deduplicated and sorted for order
        independence. Falls back to the whole normalized evidence when no
        line carries a known failure marker — an unrecognized format must
        still fingerprint deterministically rather than collapse to a
        constant that would look like a stall for every distinct failure.
    """
    markers = tuple(m.lower() for m in FAILURE_MARKERS)
    hits = [
        _normalize(line)
        for line in evidence.splitlines()
        if any(marker in line.lower() for marker in markers)
    ]
    if not hits:
        hits = [_normalize(line) for line in evidence.splitlines() if line.strip()]
    return sorted(set(hits))[:max_lines]


def failure_fingerprint(evidence: str, *, max_lines: int = 40, length: int = 12) -> str:
    """Hash the failure signal in *evidence* into a short stable identifier.

    Two attempts that fail the same way produce the same fingerprint even
    when the generated code differs; two attempts that fail differently
    produce different ones. That is exactly the signal a stall guard needs.

    Args:
        evidence: Raw verifier output.
        max_lines: Cap forwarded to :func:`failure_lines`.
        length: Number of leading hex characters to keep.

    Returns:
        A lowercase hex digest prefix. Empty evidence yields ``"empty"``.
    """
    lines = failure_lines(evidence, max_lines=max_lines)
    if not lines:
        return "empty"
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    return digest[:length]
