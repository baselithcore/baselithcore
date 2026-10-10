"""Text normalisation for guardrail pattern matching.

Regex guards match what they are written against. An attacker who writes
``1gn0r3 pr3v10us 1nstruct10ns``, hides a zero-width space inside a
keyword, swaps a Latin ``o`` for a Cyrillic ``о``, types in fullwidth
``ｉｇｎｏｒｅ`` or spaces the letters out (``i g n o r e``) walks past every
pattern. This module derives *matching views* of the input that undo those
tricks; the guard runs its patterns over each view and reports which one
matched. The original text is never modified — the views exist only for
matching and are discarded afterwards.

The views, cheapest first:

* ``normalized`` — Unicode NFKC (folds fullwidth, mathematical alphanumerics,
  ligatures, compatibility forms) and removal of every format character
  (Unicode category ``Cf``: zero-width space/joiners, word joiner, BOM, soft
  hyphen, bidi embeddings/overrides/isolates). Non-Latin scripts survive, so
  the multilingual patterns run here.
* ``deobfuscated`` — ``normalized`` plus a confusables fold (Cyrillic/Greek
  lookalikes to Latin), leetspeak de-substitution inside alphanumeric tokens
  (``0→o 1→i 3→e 4→a 5→s 7→t @→a $→s``, with ``11→ll`` first so ``a11``
  reads as ``all``) and collapsing of single-letter runs
  split by one separator (``i.g.n.o.r.e``, ``i g n o r e``).
* ``squashed`` — letters only, produced only when letter-spacing was found:
  ``i g n o r e p r e v i o u s`` has no word boundaries left, so it is
  matched against a short list of whole-phrase patterns.
* ``native`` — ``normalized`` with letter spacing collapsed but *without* the
  confusables fold, for non-ASCII text, produced only when it differs from
  ``normalized``. Inside a token that already holds a Cyrillic letter,
  Latin lookalikes and leet digits are folded the other way, *into*
  Cyrillic (``игнoрируй`` with a Latin ``o``, ``3абудь``). The ``deobfuscated``
  view turns Russian into Latin lookalikes, so this is where the Russian
  patterns see spaced-out or character-swapped Russian.
* ``native-squashed`` — letters and combining marks of ``native`` (any
  script, unfolded), produced only when letter-spacing was found in
  non-ASCII text; matched against whole-phrase forms of the non-Latin
  patterns (``и г н о р и р у й в с е …`` with no word gaps at all).

Spacing collapse treats a combining mark (Unicode ``Mn``/``Mc``/``Me``) as
part of the letter before it, and first removes a separator found in front
of a mark — a mark never follows a space in real text — so Devanagari vowel
signs and viramas rejoin their consonant (``प ि छ ल े`` → ``पिछले``).

Every transform is a single linear pass; the input is capped at
:data:`MAX_NORMALIZE_CHARS` (NFKC can expand a character up to 18x, so the
cap is re-applied after it).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import cache

#: Hard ceiling on the length of any derived view.
MAX_NORMALIZE_CHARS = 1_000_000

# Confusables: Cyrillic, Greek and assorted lookalikes folded to the Latin
# letter they imitate. Deliberately short — the goal is the letters attackers
# actually swap into keywords, not the full Unicode confusables table.
_CONFUSABLES: dict[str, str] = {
    # Cyrillic lower / upper
    "а": "a", "в": "b", "е": "e", "ё": "e", "к": "k", "м": "m", "н": "h",
    "о": "o", "р": "p", "с": "c", "т": "t", "у": "y", "х": "x", "і": "i",
    "ї": "i", "ј": "j", "ѕ": "s", "ԁ": "d", "һ": "h", "ӏ": "l", "ԛ": "q",
    "ԝ": "w", "ɡ": "g",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "Х": "X", "У": "Y", "І": "I", "Ј": "J",
    "Ѕ": "S",
    # Greek lower / upper
    "α": "a", "β": "b", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o",
    "ρ": "p", "τ": "t", "υ": "u", "χ": "x", "γ": "y",
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K",
    "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
    # Latin/other lookalikes NFKC leaves alone
    "ı": "i", "ȷ": "j", "օ": "o", "ց": "g",
}  # fmt: skip
_CONFUSABLES_TABLE = str.maketrans(_CONFUSABLES)

_LEET_I = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s",
                         "7": "t", "@": "a", "$": "s"})  # fmt: skip

# An alphanumeric token that mixes letters with leet digits/symbols. Pure
# numbers ("2024", "v1") are left alone: only a token that also contains a
# letter is de-substituted, which keeps prices, dates and versions intact.
_LEET_TOKEN_RE = re.compile(r"[A-Za-z0-9@$]+")
_LEET_CHARS = frozenset("013457@$")

_SEPARATORS = r"[ .\-_*|/~+·,]"
_SEPARATOR_RE = re.compile(_SEPARATORS)
_NON_LETTER_RE = re.compile(r"[^a-z]+")

# Latin lookalikes and leet digits folded *into* Cyrillic, applied only inside
# a token that already holds a Cyrillic letter: the reverse of the
# confusables fold, for the ``native`` view. ``3`` is ``з`` (its visual twin),
# not ``е``: Russian has no use for a Latin-style ``3→e``.
_CYRILLIC_UNFOLD = str.maketrans({
    "a": "а", "c": "с", "e": "е", "h": "н", "k": "к", "m": "м", "o": "о",
    "p": "р", "t": "т", "x": "х", "y": "у",
    "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К", "M": "М",
    "O": "О", "P": "Р", "T": "Т", "X": "Х", "Y": "У",
    "0": "о", "3": "з", "4": "ч", "6": "б", "@": "а",
})  # fmt: skip
_TOKEN_RE = re.compile(r"[\w@]+")
_CYRILLIC_RE = re.compile(r"[\u0400-\u04ff]")


@cache
def _mark_class() -> str:
    """Character-class body for every BMP combining mark (``Mn``/``Mc``/``Me``).

    Built from the interpreter's own Unicode database on first use, so ASCII
    traffic never pays for it. Every script in the multilingual table lives
    in the BMP; nothing below U+0300 is a mark.
    """
    ranges: list[tuple[int, int]] = []
    for cp in range(0x300, 0x10000):
        if unicodedata.category(chr(cp))[0] != "M":
            continue
        if ranges and ranges[-1][1] == cp - 1:
            ranges[-1] = (ranges[-1][0], cp)
        else:
            ranges.append((cp, cp))
    return "".join(chr(lo) if lo == hi else f"{chr(lo)}-{chr(hi)}" for lo, hi in ranges)


@cache
def _spaced_run_re() -> re.Pattern[str]:
    """Three or more single letters each split by exactly one separator.

    A unit is a letter with its trailing combining marks (``पि``). Letters,
    marks and separators are disjoint classes, so the match is linear.
    """
    marks = _mark_class()
    unit = rf"[^\W\d_][{marks}]*"
    return re.compile(
        rf"(?<![\w{marks}]){unit}{_SEPARATORS}"
        rf"(?:{unit}{_SEPARATORS})+{unit}(?![\w{marks}])"
    )


@cache
def _spaced_mark_re() -> re.Pattern[str]:
    """One separator between a letter (or mark) and a combining mark.

    A combining mark never follows a separator in real text — it belongs to
    the character before it — so the separator is spacing to undo, even in
    a two-character word (``क ो``).
    """
    marks = _mark_class()
    return re.compile(rf"(?<=[\w{marks}]){_SEPARATORS}(?=[{marks}])")


@cache
def _non_letter_or_mark_re() -> re.Pattern[str]:
    """Everything but letters and combining marks (for ``native-squashed``)."""
    return re.compile(rf"(?:[^\w{_mark_class()}]|[\d_])+")


@dataclass(frozen=True)
class MatchingView:
    """One derived view of the input, used only for pattern matching.

    Attributes:
        label: Which transform produced it (``normalized``, ``deobfuscated``,
            ``squashed``).
        text: The transformed text.
        squashed: Whether the view has no word boundaries left (match it
            against the whole-phrase patterns, not the word patterns).
        native: Whether the view keeps the scripts the confusables fold
            rewrites (Cyrillic/Greek) unfolded: their patterns, skipped on
            the ``deobfuscated`` views, run here.
    """

    label: str
    text: str
    squashed: bool = False
    native: bool = False


def strip_format_chars(text: str) -> str:
    """Remove every Unicode format (``Cf``) character.

    Covers zero-width space/non-joiner/joiner (U+200B-U+200D), word joiner
    (U+2060), BOM (U+FEFF), soft hyphen and all bidi controls.

    Args:
        text: Input text.

    Returns:
        The text without format characters.
    """
    if text.isascii():
        return text
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def nfkc_clean(text: str) -> str:
    """NFKC-normalise and strip format characters, capped in length.

    Args:
        text: Input text.

    Returns:
        The ``normalized`` view.
    """
    capped = text[:MAX_NORMALIZE_CHARS]
    if capped.isascii():
        return capped
    return strip_format_chars(unicodedata.normalize("NFKC", capped))[
        :MAX_NORMALIZE_CHARS
    ]


def _deleet(text: str, table: dict[int, str]) -> str:
    """De-substitute leetspeak inside mixed letter/digit tokens."""

    def fix(match: re.Match[str]) -> str:
        token = match.group(0)
        if token.isalpha() or token.isdigit() or _LEET_CHARS.isdisjoint(token):
            return token
        if not any(ch.isalpha() for ch in token):
            return token
        # "11" is almost always "ll" (a11, wi11, fo11ow); a lone 1 reads "i".
        return token.replace("11", "ll").translate(table)

    return _LEET_TOKEN_RE.sub(fix, text)


def _collapse_spaced(text: str) -> tuple[str, bool]:
    """Join single-letter runs split by one separator; report if any found.

    Spaced-out combining marks are first reattached to their base, so a
    Devanagari word spaced code point by code point (``प ि छ ल े``) is
    first read as its letters (``पि छ ले``), then joined.
    """
    found = False
    if not text.isascii():
        text, marks_rejoined = _spaced_mark_re().subn("", text)
        found = marks_rejoined > 0

    def join(match: re.Match[str]) -> str:
        nonlocal found
        found = True
        return _SEPARATOR_RE.sub("", match.group(0))

    return _spaced_run_re().sub(join, text), found


def _unfold_to_cyrillic(text: str) -> str:
    """Fold Latin lookalikes and leet digits into Cyrillic, per mixed token.

    Only a token that already holds a Cyrillic letter is touched, so Latin
    words, numbers and code in the same text are left alone.
    """

    def fix(match: re.Match[str]) -> str:
        token = match.group(0)
        if token.isascii() or not _CYRILLIC_RE.search(token):
            return token
        return token.translate(_CYRILLIC_UNFOLD)

    return _TOKEN_RE.sub(fix, text)


def matching_views(text: str) -> list[MatchingView]:
    """Derive the normalised matching views of ``text``.

    Views identical to the original (or to an earlier view) are omitted, so
    plain ASCII prose costs nothing beyond one leet/spacing pass.

    Args:
        text: The original input. Never modified.

    Returns:
        Distinct views in cheapest-first order; empty when every transform
        is a no-op.
    """
    views: list[MatchingView] = []
    seen = {(text, False, False)}

    def add(
        label: str, value: str, *, squashed: bool = False, native: bool = False
    ) -> None:
        if value and (value, squashed, native) not in seen:
            seen.add((value, squashed, native))
            views.append(MatchingView(label, value, squashed, native))

    normalized = nfkc_clean(text)
    add("normalized", normalized)

    folded = normalized.translate(_CONFUSABLES_TABLE)
    collapsed, spaced = _collapse_spaced(_deleet(folded, _LEET_I))
    add("deobfuscated", collapsed)
    if spaced:
        add("squashed", _NON_LETTER_RE.sub("", collapsed.lower()), squashed=True)

    if normalized.isascii():
        return views
    # The fold above rewrote Cyrillic/Greek into Latin lookalikes; collapse
    # the spacing again on the unfolded text so those scripts' patterns see
    # their own words. Collapse first, then fold into Cyrillic: a spaced-out
    # Latin lookalike only shares a token with Cyrillic once rejoined. Skipped
    # when identical to a view already scanned with those patterns.
    native, native_spaced = _collapse_spaced(normalized)
    native = _unfold_to_cyrillic(native)
    if native not in (normalized, collapsed):
        add("native", native, native=True)
    if native_spaced and not native.isascii():
        squashed_native = _non_letter_or_mark_re().sub("", native).lower()
        add("native-squashed", squashed_native, squashed=True, native=True)
    return views


#: Whole-phrase patterns for the ``squashed`` view (no spaces left). Kept to
#: long, unambiguous phrases: squashing erases word boundaries, so a short
#: pattern would match across unrelated words.
SQUASHED_INJECTION_PATTERNS: tuple[str, ...] = (
    r"ignore(all|the|any)?(previous|prior|above)instructions?",
    r"disregard(all)?(previous|prior|above)(instructions?|guidance|rules)",
    r"forget(everything|all)(you|instructions?|training)",
    r"(reveal|show|print|repeat|output|display|dump)(me)?your"
    r"(system|initial|original)?(prompt|instructions)",
    r"newsystemprompt",
    r"doanythingnow",
    r"(developer|god|jailbreak)mode(enabled|activated)",
    r"youhaveno(restrictions|rules|limits|filters|guidelines)",
)
COMPILED_SQUASHED_PATTERNS = [re.compile(p) for p in SQUASHED_INJECTION_PATTERNS]

__all__ = [
    "COMPILED_SQUASHED_PATTERNS",
    "MAX_NORMALIZE_CHARS",
    "SQUASHED_INJECTION_PATTERNS",
    "MatchingView",
    "matching_views",
    "nfkc_clean",
    "strip_format_chars",
]
