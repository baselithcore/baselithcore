"""Pattern-side spelling variants for the multilingual injection patterns.

A keyword can be written many ways a reader still recognises: Russian in
Latin lookalikes (``Bce``) or in transliteration (``vse``), Arabic with an
Arabizi digit for a letter (``الت3ليمات``) or without its hamza
(``الاوامر``), Chinese with a homophone or a variant character (``乎略``,
``旨令``, ``系桶提示``). Rather than derive one more text view per
convention, each expander here rewrites a pattern *source*, widening its
keywords to the variants, so a single compiled pattern accepts them all and
the guard scans no extra view.

Every expander adds character classes and bounded alternations only — no
new quantifier — so a linear pattern stays linear. The guard reports a
match under the source as written (see
:func:`baselith.core.guardrails.multilingual.pattern_source`), not under
its widened form.
"""

from __future__ import annotations

import re

# ----------------------------------------------------------------------------
# Russian: Latin lookalikes (same script trick as the confusables fold, run in
# reverse on the pattern) and Latin transliteration.

#: Latin letters and digits that stand in for a Cyrillic letter, upright or
#: italic/handwritten (``u`` for ``и``, ``r`` for ``г``, ``n`` for ``п``,
#: ``m`` for ``т``, ``6`` for ``б``, ``3`` for ``з``). Matching is
#: case-insensitive, so ``b`` also covers ``B`` for ``в``.
_CYRILLIC_LOOKALIKES: dict[str, str] = {
    "а": "a@", "б": "6", "в": "b", "г": "r", "д": "dg", "е": "eёë",
    "ё": "eеë", "з": "3", "и": "u", "й": "иu", "к": "k", "м": "m", "н": "h",
    "о": "o0", "п": "n", "р": "p", "с": "c", "т": "tm", "у": "y", "х": "x",
    "ц": "u", "ч": "4", "ъ": "b", "ь": "b", "э": "3",
}  # fmt: skip
#: Cyrillic letters imitated by a sequence of Latin characters.
_CYRILLIC_LOOKALIKE_SEQUENCES: dict[str, tuple[str, ...]] = {
    "л": ("ji", "jl"),
    "ш": ("w", "lli", "iii"),
    "щ": ("ш", "w", "lli", "lll"),
    "ы": ("bi", "bl", "b1"),
}

#: Latin spellings of each Cyrillic letter across the transliteration schemes
#: people actually type — scholarly/ISO (``ž``-less ASCII forms), BGN/PCGN,
#: passport (``ts``, ``kh``, ``shch``) and informal chat (``j`` for ``й``,
#: ``c`` for ``ц``, ``4`` for ``ч``, ``w`` for ``в``). An empty spelling
#: means the letter is often dropped (the soft and hard signs, a final
#: ``й``). Longest first, so the engine tries the full spelling first.
_CYRILLIC_TRANSLIT: dict[str, tuple[str, ...]] = {
    "а": ("a",), "б": ("b",), "в": ("v", "w"), "г": ("gh", "g"),
    "д": ("d",), "е": ("ye", "je", "e"), "ё": ("yo", "jo", "io", "e"),
    "ж": ("zh", "j", "z"), "з": ("z",), "и": ("i", "y"),
    "й": ("y", "j", "i", ""), "к": ("k",), "л": ("l",), "м": ("m",),
    "н": ("n",), "о": ("o",), "п": ("p",), "р": ("r",), "с": ("s",),
    "т": ("t",), "у": ("ou", "u"), "ф": ("ph", "f"),
    "х": ("kh", "h", "x"), "ц": ("ts", "tz", "c", "z"),
    "ч": ("tch", "ch", "4"), "ш": ("sch", "sh"),
    "щ": ("shch", "sch", "shh", "sh"), "ъ": ("'", "’", ""),
    "ы": ("yi", "y", "i"), "ь": ("'", "’", ""), "э": ("eh", "e"),
    "ю": ("yu", "ju", "iu", "u"), "я": ("ya", "ja", "ia", "a"),
}  # fmt: skip

# ----------------------------------------------------------------------------
# Arabic: the Arabizi digit convention, Arabic-Indic/Persian digit forms of
# it, and the orthographic variants informal writing uses without thinking
# (hamza dropped from alef, taa marbuta as haa, alef maqsura as yaa).

#: Arabic letter -> the characters that stand in for it. ``3`` is ``ع``,
#: ``7`` ``ح``, ``5`` ``خ``, ``6`` ``ط``, ``9`` ``ص`` (and ``ق`` in Gulf
#: usage), ``8`` ``ق``, ``2`` the hamza (and ``ق`` where it is pronounced as
#: one, as in Cairo and Beirut); each also in its Arabic-Indic and Persian
#: form, which NFKC leaves alone.
_ARABIC_VARIANTS: dict[str, str] = {
    "ع": "3٣۳", "ح": "7٧۷", "خ": "5٥۵", "ط": "6٦۶",
    "ص": "9٩۹", "ق": "8٨۸9٩۹2٢۲",
    "أ": "اإآ2٢۲", "إ": "اأآ2٢۲", "آ": "اأإ2٢۲", "ء": "2٢۲",
    "ئ": "ىي2٢۲", "ؤ": "و2٢۲",
    "ة": "ه", "ى": "ي", "ي": "ى",
}  # fmt: skip
#: Visual stand-ins outside the Arabizi convention, each one a pair the
#: Unicode confusables table (UTS #39) lists: alef is a vertical stroke, like
#: ``1``, ``l``, ``|`` and the Arabic-Indic and Persian one (``١``, ``۱``) —
#: ``i`` is there because the patterns see lowercased text, where ``I`` is
#: ``i``; heh is a loop, like ``o``, ``0`` and the Arabic-Indic and Persian
#: five (``٥``, ``۵``, which the ``normalized`` view folds to ``5``); taa
#: marbuta is heh with two dots (``ö``). Digits that resemble no Arabic
#: letter are not mapped: ``٧`` is ``V``, ``٨`` is ``Λ``, ``٠`` a full stop,
#: and ASCII ``7`` — ``ح`` in Arabizi — looks like none of them.
_ARABIC_VISUAL: dict[str, str] = {
    "ا": "1١۱l|i", "ه": "o0٥۵5", "ة": "o0٥۵5ö",
}  # fmt: skip
_ARABIC_VARIANTS = {
    letter: _ARABIC_VARIANTS.get(letter, "") + _ARABIC_VISUAL.get(letter, "")
    for letter in (*_ARABIC_VARIANTS, *_ARABIC_VISUAL)
}
#: Letters written as an Arabizi digit plus an apostrophe (``7'`` is ``خ``,
#: ``6'`` is ``ظ``, ``3'`` is ``غ``).
_ARABIC_VARIANT_SEQUENCES: dict[str, tuple[str, ...]] = {
    "خ": ("7'", "7’"),
    "ظ": ("6'", "6’", "6"),
    "غ": ("3'", "3’", "8"),
}

# ----------------------------------------------------------------------------
# Chinese: homophones and variant characters for the keywords of the zh
# patterns. A curated, per-keyword table — not a pinyin engine — because a
# homophone is only safe where no common word results: ``是``/``事`` sound
# like ``视``/``示`` but would turn ``只是`` and ``无事`` into keywords, so
# they are left out, as is ``式`` (``显式`` means "explicit"). Digits are in
# where Chinese chat already reads a number as its sound (数字谐音): ``0``
# (零, líng) for 令, ``1`` (一, yī) for 以, ``5`` (五, wǔ) for 无 — ``指0``,
# ``1前``, ``5视``. ``4`` for 示 (shì, through the slang ``4`` = 是) is left
# out for the same reason as 是: ``提4个问题`` is everyday text.

#: Keyword spellings (simplified, traditional) -> per-character classes. Both
#: spellings of a pair share one class string, so ``(无视|無視)`` widens to
#: two identical branches, which :func:`expand_zh_homophones` then merges —
#: a failing match would otherwise retry the same branch twice.
_ZH_VARIANT_GROUPS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("忽略",), "[忽乎呼惚唿][略掠畧]"),
    (("无视", "無視"), "[无無毋吴5五][视視示]"),
    (("忽视", "忽視"), "[忽乎呼惚][视視示]"),
    (("理会", "理會"), "[理里][会會汇匯]"),
    (("之前",), "[之知支][前钱錢]"),
    (("以前",), "[以已1][前钱錢]"),
    (("先前",), "[先仙][前钱錢]"),
    (("指令",), "[指旨纸紙止][令冷领領伶铃鈴0〇零]"),
    (("指示",), "[指旨纸紙止][示视視]"),
    (("提示词", "提示詞"), "[提题題][示视視][词詞辞辭]"),
    (("提示",), "[提题題][示视視]"),
    (("规则", "規則"), "[规規归歸龟龜][则則择擇泽澤]"),
    (("忘记", "忘記"), "[忘望][记記纪紀计計]"),
    (("设定", "設定"), "[设設][定订訂]"),
    (("现在", "現在"), "[现現][在再]"),
    (("显示", "顯示"), "[显顯险險][示视視]"),
    (("输出", "輸出"), "[输輸][出初]"),
    (("告诉", "告訴"), "告[诉訴速]"),
    (("泄露", "洩露"), "[泄洩谢謝卸][露漏]"),
    (("重复", "重複"), "重[复複覆]"),
    (("系统", "系統"), "[系係细細戏戲][统統桶筒]"),
)
_ZH_VARIANTS: dict[str, str] = {
    spelling: classes for spellings, classes in _ZH_VARIANT_GROUPS
    for spelling in spellings
}  # fmt: skip
_ZH_KEYWORD_RE = re.compile(
    "|".join(sorted(map(re.escape, _ZH_VARIANTS), key=len, reverse=True))
)
#: An innermost group (no nested parentheses), for merging equal branches.
_INNER_GROUP_RE = re.compile(r"\(([^()]*)\)")


def _alternation(options: tuple[str, ...]) -> str:
    """A non-capturing group matching any of ``options`` (``""`` allowed)."""
    if len(options) == 1 and options[0]:
        return re.escape(options[0])
    return "(?:" + "|".join(re.escape(o) if o else "" for o in options) + ")"


def _expand_letters(
    source: str, classes: dict[str, str], sequences: dict[str, tuple[str, ...]]
) -> str:
    """Widen each listed letter of ``source`` to its variants.

    Escapes and character classes are copied as written: a class already
    lists the characters it accepts (a script range in a lookbehind, say),
    and a widened letter inside one would no longer be a literal.
    """
    parts: list[str] = []
    in_class = escaped = False
    for ch in source:
        if escaped or in_class or ch in "\\[":
            parts.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == "[":
                in_class = True
            elif ch == "]" and in_class:
                in_class = False
        elif ch in sequences:
            single = tuple(classes.get(ch, ""))
            parts.append(_alternation((ch, *single, *sequences[ch])))
        elif ch in classes:
            parts.append(f"[{ch}{re.escape(classes[ch])}]")
        else:
            parts.append(ch)
    return "".join(parts)


def expand_cyrillic_lookalikes(source: str) -> str:
    """Let every Cyrillic letter of a pattern also match its Latin lookalikes.

    ``все`` becomes ``[вb][сc][еeёë]``, so ``Bce`` — Latin letters only —
    matches, as do mixed tokens (``npeдыдущue``) and leet digits (``3абудь``).
    An English word can only match if it spells a whole Russian override
    phrase letter by letter.

    Args:
        source: Regex source whose Cyrillic letters are all literals.

    Returns:
        The pattern with each Cyrillic letter widened to its lookalikes.
    """
    return _expand_letters(source, _CYRILLIC_LOOKALIKES, _CYRILLIC_LOOKALIKE_SEQUENCES)


def transliterate_cyrillic(source: str) -> str:
    """Rewrite a Russian pattern into its Latin transliterations.

    ``забудь(те)?\\s+(все\\s+)?правила`` matches ``zabud vse pravila``,
    ``zabud' vse pravila`` and ``zabud'te pravila``; ``предыдущие`` matches
    ``predydushchie``, ``predidushie`` and ``predyduschie``. Every Cyrillic
    letter becomes an alternation of its spellings (an empty one where the
    letter is often dropped); Latin letters and regex syntax pass through,
    so English alternatives in the source (``previous``, ``dan``) stay.

    Args:
        source: Regex source whose Cyrillic letters are all literals.

    Returns:
        A pattern over Latin text.

    Raises:
        ValueError: The source has a character class.
    """
    if "[" in source:
        raise ValueError(f"cannot expand a pattern with a class: {source!r}")
    return "".join(
        _alternation(_CYRILLIC_TRANSLIT[ch]) if ch in _CYRILLIC_TRANSLIT else ch
        for ch in source.lower()
    )


def expand_arabic_variants(source: str) -> str:
    """Let an Arabic pattern match Arabizi digits and informal spellings.

    ``التعليمات`` also matches ``الت3ليمات`` and ``الت٣ليمات`` (a digit
    *replacing* ``ع``, the Arabizi way of writing it); ``الأوامر`` matches
    ``الاوامر`` and ``ال2وامر``; ``السابقة`` matches ``السابقه`` and
    ``السا8قة``; and alef and heh accept their visual stand-ins
    (``تج1هل``, ``تجا0ل``). A digit *inserted* into a word (``تجا1هل``) is a
    different trick, undone by the ``deobfuscated`` view.

    Args:
        source: Regex source whose Arabic letters are all literals.

    Returns:
        The pattern with each Arabic letter widened to its variants.
    """
    return _expand_letters(source, _ARABIC_VARIANTS, _ARABIC_VARIANT_SEQUENCES)


def expand_zh_homophones(source: str) -> str:
    """Widen the Chinese keywords of a pattern to homophones and variants.

    ``忽略`` also matches ``乎略`` and ``忽掠``, ``指令`` matches ``旨令`` and
    ``指冷``, ``系统提示`` matches ``系桶提示`` and ``系统题示``. Only the
    keywords listed in the curated table are widened; everything else in the
    source — including its ``[^。！？\\n]`` gap classes, which hold no
    keyword — is left as written. Branches of a group that widen to the same
    classes (``无视|無視``) are merged into one.

    Args:
        source: Regex source of a Chinese pattern.

    Returns:
        The pattern with each listed keyword replaced by its classes.
    """
    widened = _ZH_KEYWORD_RE.sub(lambda m: _ZH_VARIANTS[m.group(0)], source)

    def merge(match: re.Match[str]) -> str:
        branches = match.group(1).split("|")
        return "(" + "|".join(dict.fromkeys(branches)) + ")"

    return _INNER_GROUP_RE.sub(merge, widened)


# ----------------------------------------------------------------------------
# Devanagari: the vowel sign ``ा`` is a bare vertical stroke on the right of
# its consonant — the danda's shape, which UTS #39 lists as confusable with
# ``l`` and ``|`` — so ``दिख1ओ`` / ``दिखlओ`` still read ``दिखाओ``. No
# Devanagari digit is a confusable of a Devanagari letter, and Hindi is not
# written with digits for letters; the one digit Hindi chat does use as a
# word, ``2`` for ``दो`` (two, and the "give" auxiliary of ``दिखा दो``), is
# spelt out in the patterns.
_DEVANAGARI_VARIANTS: dict[str, str] = {"ा": "।|l1"}


def expand_devanagari_variants(source: str) -> str:
    """Let a Hindi pattern's ``ा`` match the strokes that imitate it.

    Args:
        source: Regex source of a Hindi pattern.

    Returns:
        The pattern with each ``ा`` outside a class widened.
    """
    return _expand_letters(source, _DEVANAGARI_VARIANTS, {})


# ----------------------------------------------------------------------------
# Pinyin: tone marks and tone digits. A pinyin pattern writes each phrase in
# braces, syllables split by ``_`` and words by a space (``{hu_lüe}``,
# ``{gao_su wo}``); everything outside braces is left as written.

#: Toneless vowel -> its four tone-marked forms (``ü`` also as ``v``, the
#: IME spelling, and as plain ``u``, how most people type it).
_PINYIN_TONES: dict[str, str] = {
    "a": "aāáǎà", "e": "eēéěè", "i": "iīíǐì", "o": "oōóǒò", "u": "uūúǔù",
    "ü": "üǖǘǚǜvu",
}  # fmt: skip
#: Between syllables and words: an optional tone digit (``hu1lve4``), then
#: any spacing (joined, spaced, ``xi'an``-style apostrophe, hyphen).
_PINYIN_JOIN = r"[1-5]?[\s'’-]*"
_PINYIN_PHRASE_RE = re.compile(r"\{([a-zü_ ]+)\}")


def _pinyin_phrase(match: re.Match[str]) -> str:
    """One braced phrase, widened (see :func:`expand_pinyin`)."""
    parts = [
        f"[{_PINYIN_TONES[ch]}]" if ch in _PINYIN_TONES else ch for ch in match.group(1)
    ]
    return "".join(_PINYIN_JOIN if p in "_ " else p for p in parts) + "[1-5]?"


def expand_pinyin(source: str) -> str:
    """Widen the braced pinyin phrases of a pattern to their typed forms.

    ``{hu_lüe}`` matches ``hulüe``, ``hu lue``, ``hūlüè``, ``hu1lve4`` and
    ``hu-lue``: each vowel also takes its tone marks, each syllable an
    optional tone digit, and syllables and words any spacing between them —
    a single class run, then a literal, so the pattern stays linear. A
    phrase ends with its optional tone digit, never with spacing, so the
    ``\\s+`` that follows in the source does not overlap it.

    Args:
        source: Regex source with ``{...}`` pinyin phrases.

    Returns:
        The pattern with every phrase widened.
    """
    return _PINYIN_PHRASE_RE.sub(_pinyin_phrase, source)


__all__ = [
    "expand_arabic_variants",
    "expand_devanagari_variants",
    "expand_pinyin",
    "expand_cyrillic_lookalikes",
    "expand_zh_homophones",
    "transliterate_cyrillic",
]
