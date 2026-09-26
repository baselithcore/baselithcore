"""Decode-and-rescan for encoded prompt-injection payloads.

``aWdub3JlIGFsbCBwcmV2aW91cyBpbnN0cnVjdGlvbnM=`` is "ignore all previous
instructions" in base64: a model decodes it happily, a regex guard sees
noise. This module finds encoded segments — base64 (standard and URL-safe),
hex (``69676e6f7265``), ``\\x``-escaped bytes and percent-encoding — decodes
them under strict size limits and hands the decoded text back to the guard
as additional views to scan.

Safety limits (all enforced per :func:`scan_views` call):

* segments shorter than 16 characters are ignored (too short to carry a
  meaningful instruction, too common in ordinary text);
* only the first :data:`MAX_DECODE_SCAN_CHARS` characters are searched;
* at most :data:`MAX_SEGMENTS` segments are decoded per level, each read up
  to :data:`MAX_SEGMENT_CHARS` characters;
* at most :data:`MAX_DECODED_BYTES` decoded bytes in total;
* decoding recurses at most :data:`MAX_DECODE_DEPTH` levels (base64 inside
  base64);
* decoded bytes must be valid UTF-8 and mostly printable, so random
  identifiers that happen to be base64-shaped are dropped, not scanned.

Every regex here is a single character class with a bounded repeat: linear.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Iterator
from dataclasses import dataclass
from urllib.parse import unquote

from .normalize import MatchingView, matching_views

MAX_DECODE_DEPTH = 2
MAX_SEGMENTS = 32
MAX_SEGMENT_CHARS = 16_384
MAX_DECODED_BYTES = 65_536
#: How far into the text the segment scanners read.
MAX_DECODE_SCAN_CHARS = 200_000
_MIN_SEGMENT_CHARS = 16
_MIN_PRINTABLE_RATIO = 0.9

_B64_RE = re.compile(r"[A-Za-z0-9+/_-]{16,}={0,2}")
_HEX_RE = re.compile(r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}){8,}(?![0-9A-Fa-f])")
_ESCAPED_HEX_RE = re.compile(r"(?:\\x[0-9A-Fa-f]{2}){8,}")
_PERCENT_RE = re.compile(r"%[0-9A-Fa-f]{2}")
_MIN_PERCENT_ESCAPES = 4


@dataclass
class _Budget:
    """Remaining decoded-byte allowance shared across one scan."""

    remaining: int = MAX_DECODED_BYTES


def _printable_text(raw: bytes) -> str | None:
    """Return ``raw`` as text when it is UTF-8 and mostly printable."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not text or not any(ch.isalpha() for ch in text):
        return None
    printable = sum(1 for ch in text if ch.isprintable() or ch in "\t\n\r")
    if printable / len(text) < _MIN_PRINTABLE_RATIO:
        return None
    return text


def _looks_base64(segment: str) -> bool:
    """Cheap pre-filter: real base64 of text mixes cases and/or digits."""
    has_upper = any(ch.isupper() for ch in segment)
    has_lower = any(ch.islower() for ch in segment)
    has_digit = any(ch.isdigit() for ch in segment)
    return (has_upper and has_lower) or (has_digit and (has_upper or has_lower))


def _decode_base64(segment: str) -> bytes | None:
    """Decode a (possibly URL-safe, unpadded) base64 segment, or ``None``."""
    body = segment.rstrip("=").replace("-", "+").replace("_", "/")
    if len(body) % 4 == 1:
        body = body[:-1]
    body += "=" * (-len(body) % 4)
    try:
        return base64.b64decode(body, validate=True)
    except (binascii.Error, ValueError):
        return None


def _decode_hex(segment: str) -> bytes | None:
    """Decode a hex (or ``\\x``-escaped hex) segment, or ``None``."""
    try:
        return bytes.fromhex(segment.replace("\\x", ""))
    except ValueError:
        return None


def _decoded_segments(text: str, budget: _Budget) -> Iterator[tuple[str, str]]:
    """Yield ``(encoding, decoded_text)`` for each decodable segment."""
    text = text[:MAX_DECODE_SCAN_CHARS]
    candidates: list[tuple[str, str]] = []
    for match in _B64_RE.finditer(text):
        segment = match.group(0)
        if len(segment) >= _MIN_SEGMENT_CHARS and _looks_base64(segment):
            candidates.append(("base64", segment))
        if len(candidates) >= MAX_SEGMENTS:
            break
    candidates.extend(("hex", m.group(0)) for m in _HEX_RE.finditer(text))
    candidates.extend(("hex", m.group(0)) for m in _ESCAPED_HEX_RE.finditer(text))

    decoded_count = 0
    for encoding, segment in candidates:
        if decoded_count >= MAX_SEGMENTS or budget.remaining <= 0:
            return
        segment = segment[:MAX_SEGMENT_CHARS]
        raw = _decode_base64(segment) if encoding == "base64" else _decode_hex(segment)
        if raw is None:
            continue
        raw = raw[: budget.remaining]
        budget.remaining -= len(raw)
        decoded_count += 1
        decoded = _printable_text(raw)
        if decoded is not None:
            yield encoding, decoded

    if len(_PERCENT_RE.findall(text, 0, MAX_SEGMENT_CHARS)) >= _MIN_PERCENT_ESCAPES:
        unquoted = unquote(text[:MAX_SEGMENT_CHARS])
        if unquoted != text[:MAX_SEGMENT_CHARS] and budget.remaining > 0:
            budget.remaining -= len(unquoted)
            yield "url", unquoted


def scan_views(text: str, *, decode: bool = True) -> list[MatchingView]:
    """All matching views of ``text``: normalised plus decoded payloads.

    The original text is *not* included — the caller scans it first. Decoded
    payloads are labelled by their encoding chain (``base64``,
    ``base64>hex``) and each gets its own normalised views
    (``base64:deobfuscated``), so an encoded leetspeak payload is still
    caught.

    Args:
        text: The original input.
        decode: Whether to run the decode-and-rescan stage.

    Returns:
        Views to scan, in order: normalised views of the original, then
        decoded payloads and their views.
    """
    views = matching_views(text)
    if not decode:
        return views
    budget = _Budget()
    frontier: list[tuple[str, str]] = [("", text)]
    for _depth in range(MAX_DECODE_DEPTH):
        next_frontier: list[tuple[str, str]] = []
        for chain, source in frontier:
            for encoding, decoded in _decoded_segments(source, budget):
                label = f"{chain}>{encoding}" if chain else encoding
                views.append(MatchingView(label, decoded))
                views.extend(
                    MatchingView(f"{label}:{v.label}", v.text, v.squashed)
                    for v in matching_views(decoded)
                )
                next_frontier.append((label, decoded))
        if not next_frontier:
            break
        frontier = next_frontier
    return views


__all__ = [
    "MAX_DECODED_BYTES",
    "MAX_DECODE_DEPTH",
    "MAX_SEGMENTS",
    "MAX_SEGMENT_CHARS",
    "scan_views",
]
