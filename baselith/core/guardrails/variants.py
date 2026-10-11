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
# they are left out, as is ``式`` (``显式`` means "explicit").

#: Keyword spellings (simplified, traditional) -> per-character classes. Both
#: spellings of a pair share one class string, so ``(无视|無視)`` widens to
#: two identical branches, which :func:`expand_zh_homophones` then merges —
#: a failing match would otherwise retry the same branch twice.
_ZH_VARIANT_GROUPS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("忽略",), "[忽乎呼惚唿][略掠畧]"),
    (("无视", "無視"), "[无無毋吴][视視示]"),
    (("忽视", "忽視"), "[忽乎呼惚][视視示]"),
    (("理会", "理會"), "[理里][会會汇匯]"),
    (("之前",), "[之知支][前钱錢]"),
    (("以前",), "[以已][前钱錢]"),
    (("先前",), "[先仙][前钱錢]"),
    (("指令",), "[指旨纸紙止][令冷领領伶铃鈴]"),
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

    Raises:
        ValueError: The source has a character class, where a widened
            letter would no longer be a literal.
    """
    if "[" in source:
        raise ValueError(f"cannot expand a pattern with a class: {source!r}")
    parts: list[str] = []
    for ch in source:
        if ch in sequences:
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

    Raises:
        ValueError: The source has a character class.
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
    ``السا8قة``... A digit *inserted* into a word (``تجا1هل``) is a
    different trick, undone by the ``deobfuscated`` view.

    Args:
        source: Regex source whose Arabic letters are all literals.

    Returns:
        The pattern with each Arabic letter widened to its variants.

    Raises:
        ValueError: The source has a character class.
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


__all__ = [
    "expand_arabic_variants",
    "expand_cyrillic_lookalikes",
    "expand_zh_homophones",
    "transliterate_cyrillic",
]
