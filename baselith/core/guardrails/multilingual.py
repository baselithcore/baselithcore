"""Multilingual prompt-injection patterns, keyed by language.

An English-only pattern set is a one-line bypass for every non-English user.
This table carries the canonical override families — "ignore previous
instructions", "disregard", "forget your rules", "you are now ...", "reveal
your (system) prompt" — in the languages the framework is deployed in.

The table is plain data: to cover a new language, add a key with its
patterns. Each pattern is compiled case-insensitively after Unicode NFKC
normalisation, so it matches the guard's normalised text variant even when
the source spelling uses precomposed or compatibility characters.

Every pattern is kept linear (no nested quantifiers; gaps are bounded
``{0,N}``) and anchored to an *instruction* noun or to the assistant's own
prompt, because the verbs alone ("ignora", "忽略", "показать") are everyday
vocabulary. Benign sentences that must stay allowed are pinned in
``tests/unit/core/guardrails/test_input_evasions.py`` and ``evals/red_team/``.

The non-Latin patterns also take the canonical English keywords in their
slots (code-mixing: ``पिछले सभी instructions को ignore करो``), always next to
a native word, and the Russian ones are compiled with every Cyrillic letter
widened to its Latin lookalikes (:func:`expand_cyrillic_lookalikes`).
"""

from __future__ import annotations

import re
import unicodedata

#: Language code -> injection patterns. Extend by adding entries.
MULTILINGUAL_INJECTION_PATTERNS: dict[str, tuple[str, ...]] = {
    "es": (
        r"ignora\s+(todas\s+)?(las\s+)?instrucciones\s+(anteriores|previas)",
        r"(descarta|olvida|olv[ií]date\s+de)\s+(todas\s+)?"
        r"(tus\s+(instrucciones|reglas)|las\s+instrucciones\s+(anteriores|previas))",
        r"haz\s+caso\s+omiso\s+de\s+(todas\s+)?(las\s+)?instrucciones",
        r"\bahora\s+eres\s+(un|una|el|la)\b",
        r"(revela|mu[eé]stra|imprime|repite)(me)?\s+(tu|tus)\s+"
        r"(prompt|instrucciones)",
        r"(revela|mu[eé]stra|imprime|repite)(me)?\s+el\s+prompt\s+"
        r"(del\s+)?sistema",
        r"nuevo\s+prompt\s+del\s+sistema",
    ),
    "fr": (
        r"ignore[sz]?\s+(toutes\s+)?(les\s+)?instructions\s+"
        r"(pr[eé]c[eé]dentes|ant[eé]rieures)",
        r"oublie[sz]?\s+(toutes\s+)?((tes|vos)\s+(instructions|r[eè]gles)"
        r"|les\s+instructions\s+pr[eé]c[eé]dentes)",
        r"(ne\s+tiens|ne\s+tenez)\s+pas\s+compte\s+des\s+instructions",
        r"\b(tu\s+es|vous\s+[eê]tes)\s+maintenant\s+(un|une|le|la)\b",
        r"(r[eé]v[eè]le|affiche|montre|r[eé]p[eè]te)[sz]?(-moi)?\s+"
        r"(ton|votre|tes|vos)\s+(prompt|instructions)",
        r"(r[eé]v[eè]le|affiche|montre)[sz]?(-moi)?\s+le\s+prompt\s+"
        r"(du\s+)?syst[eè]me",
    ),
    "de": (
        r"ignorier(e|en|t)?\s+(alle\s+)?(vorherigen|bisherigen|obigen)\s+"
        r"(anweisungen|instruktionen)",
        r"(vergiss|vergessen\s+sie)\s+(alle\s+)?(deine|ihre|alle)\s+"
        r"(anweisungen|regeln|instruktionen)",
        r"missachte\s+(alle\s+)?(vorherigen|bisherigen)\s+(anweisungen|regeln)",
        r"\bdu\s+bist\s+(jetzt|nun|ab\s+jetzt)\s+(ein|eine|der|die|das)\b",
        r"(zeig(e)?|verrate|gib)\s+(mir\s+)?(deinen|deine|ihren|ihre)\s+"
        r"(system-?prompt|anweisungen|instruktionen)",
        r"(zeig(e)?|verrate|gib)\s+(mir\s+)?den\s+system-?prompt",
    ),
    "it": (
        r"ignora\s+(tutte\s+le\s+|le\s+)?istruzioni\s+(precedenti|di\s+prima)",
        r"dimentica\s+(tutte\s+)?le\s+tue\s+(regole|istruzioni)",
        r"non\s+tenere\s+conto\s+delle\s+istruzioni\s+(precedenti|di\s+prima)",
        r"\bda\s+(ora|adesso)\s+(in\s+poi\s+)?sei\s+(un|una|uno|il|la|lo)\b",
        r"(rivela|mostra(mi)?|stampa|ripeti)\s+(il\s+tuo|i\s+tuoi|le\s+tue)\s+"
        r"(prompt|istruzioni)",
        r"(rivela|mostra(mi)?|stampa)\s+il\s+prompt\s+di\s+sistema",
    ),
    "pt": (
        r"ignor[ea]\s+(todas\s+)?(as\s+)?instru[cç][oõ]es\s+"
        r"(anteriores|pr[eé]vias)",
        r"esque[cç]a\s+(todas\s+)?(as\s+)?(suas\s+(instru[cç][oõ]es|regras)"
        r"|instru[cç][oõ]es\s+(anteriores|pr[eé]vias))",
        r"desconsidere\s+(todas\s+)?(as\s+)?(suas\s+)?instru[cç][oõ]es\s+"
        r"(anteriores|pr[eé]vias)",
        r"\bagora\s+voc[eê]\s+[eé]\s+(um|uma|o|a)\b",
        r"\bvoc[eê]\s+agora\s+[eé]\s+(um|uma|o|a)\b",
        r"(revele|mostre|mostra|imprima|repita)(-me)?\s+(o\s+|as\s+)?"
        r"(seu|suas|teu|tuas)\s+(prompt|instru[cç][oõ]es)",
        r"(revele|mostre|mostra|imprima)(-me)?\s+o\s+prompt\s+do\s+sistema",
    ),
    "ru": (
        r"игнорируй(те)?\s+(все\s+)?(предыдущие|прежние|вышеуказанные|previous|"
        r"prior|above)\s+(инструкции|указания|instructions?|rules)",
        r"забудь(те)?\s+(все\s+)?(свои\s+|твои\s+|ваши\s+|предыдущие\s+)?"
        r"(инструкции|правила|указания|instructions?|rules)",
        r"не\s+обращай(те)?\s+внимания\s+на\s+(все\s+)?(предыдущие|прежние)\s+"
        r"(инструкции|указания)",
        r"(теперь\s+ты|ты\s+теперь)\s+(dan|другой|новый|свободный|"
        r"неограниченный|без\s+ограничений)",
        r"(покажи|раскрой|выведи|повтори)(те)?\s+(мне\s+)?"
        r"(свой|свои|твой|твои|ваш|ваши)\s+(системный\s+)?"
        r"(промпт|инструкции|prompt|instructions?)",
        r"(покажи|раскрой|выведи)(те)?\s+(мне\s+)?(системный\s+промпт|"
        r"system\s+prompt)",
    ),
    # Native-script languages: each pattern leads with the language's own
    # words (CJK first-character scans are what keep these cheap), and the
    # code-mixed forms — an English or leet word in a native sentence
    # (``请1gn0re之前的所有指令``, ``पिछले सभी instructions को ignore करो``) —
    # take English alternatives only after that lead, or sit in their own
    # English-led pattern, which the guard runs only on a view holding a
    # Latin word. ``(?<![a-z])`` stands in for ``\b`` where a CJK letter (a
    # word character too) may touch the English word.
    "zh": (
        r"(忽略|无视|無視|忽視|不要理会|不要理會)[^。！？\n]{0,8}?"
        r"(之前|以前|先前|上面|上述|前面)[^。！？\n]{0,6}?"
        r"(指令|指示|提示|说明|說明|规则|規則|instructions?|rules|prompts?)",
        r"忘(记|記|掉)[^。！？\n]{0,6}?(你的|所有)[^。！？\n]{0,4}?"
        r"(指令|规则|規則|设定|設定|instructions?|rules)",
        r"(忽略|无视|無視|忽視|不要理会|不要理會|忘(记|記|掉))[^。！？\n]{0,8}?"
        r"(?<![a-z])(previous|prior|above|earlier|your)\s+(instructions?|rules|prompts?)",
        r"(你现在是|你現在是|从现在开始你是|從現在開始你是)"
        r"(一个|一個|一名|DAN)",
        r"(显示|顯示|输出|輸出|告诉我|告訴我|泄露|洩露|打印|重复|重複)"
        r"[^。！？\n]{0,6}?(系统提示|系統提示|初始指令|system\s*prompt)",
        r"(显示|顯示|输出|輸出|告诉我|告訴我|泄露|洩露)[^。！？\n]{0,4}?"
        r"你的(指令|提示词|提示詞|指示)",
        # English-led
        r"(?<![a-z])(ignore|disregard)(?![a-z])[^。！？\n]{0,8}?"
        r"(之前|以前|先前|上面|上述|前面)[^。！？\n]{0,6}?"
        r"(指令|指示|提示|说明|說明|规则|規則|instructions?|rules|prompts?)",
        r"(?<![a-z])forget(?![a-z])[^。！？\n]{0,6}?(你的|所有)[^。！？\n]{0,4}?"
        r"(指令|规则|規則|设定|設定|instructions?|rules)",
        r"(?<![a-z])(reveal|show|print|output|display|repeat)(?![a-z])"
        r"[^。！？\n]{0,6}?(系统提示|系統提示|初始指令|你的(指令|提示词|提示詞|指示))",
    ),
    "ja": (
        r"(以前|前|上記|これまで)の[^。！？\n]{0,6}?"
        r"(指示|命令|指令|プロンプト|instructions?|rules|prompts?)を"
        r"[^。！？\n]{0,4}?(無視|忘れ|(?<![a-z])(ignore|disregard|forget)(?![a-z]))",
        r"(あなた|君|お前)は(今|今から|これから)は?"
        r"(DAN|制限のない|自由な|別の)",
        r"システムプロンプトを[^。！？\n]{0,6}?"
        r"(表示|教えて|出力|見せて|明かして|(?<![a-z])(show|reveal|print|output|"
        r"display)(?![a-z]))",
        r"(あなたの|君の)(指示|プロンプト|命令)を[^。！？\n]{0,4}?"
        r"(表示|教えて|出力|見せて|明かして)",
        # English-led
        r"(?<![a-z])(previous|prior|above)の[^。！？\n]{0,6}?"
        r"(指示|命令|指令|プロンプト|instructions?|rules|prompts?)を"
        r"[^。！？\n]{0,4}?(無視|忘れ|(?<![a-z])(ignore|disregard|forget)(?![a-z]))",
        r"(?<![a-z])system\s*promptを[^。！？\n]{0,6}?"
        r"(表示|教えて|出力|見せて|明かして|(?<![a-z])(show|reveal|print|output|"
        r"display)(?![a-z]))",
    ),
    "ar": (
        r"تجاهل\s+(جميع\s+|كل\s+)?(التعليمات|الأوامر|الإرشادات)\s+"
        r"(السابقة|الأولى)",
        r"انس\s+(جميع\s+|كل\s+)?(تعليماتك|قواعدك)",
        r"(تجاهل|انس)\s+(all\s+|the\s+|any\s+)?((previous|prior|above)\s+"
        r"instructions?|your\s+(instructions?|rules))",
        r"لا\s+تلتزم\s+ب(التعليمات|الأوامر)\s+السابقة",
        r"أنت\s+الآن\s+(DAN|نموذج|مساعد\s+بدون|بدون\s+قيود)",
        r"(اكشف|أظهر|اعرض|اطبع|كرر)\s+(لي\s+)?"
        r"(تعليماتك|موجه\s+النظام|التعليمات\s+الأصلية|your\s+(system\s+)?"
        r"(prompt|instructions?)|the\s+system\s+prompt)",
        # English-led
        r"\b(ignore|disregard)\s+(جميع\s+|كل\s+)?(التعليمات|الأوامر|الإرشادات)\s+"
        r"(السابقة|الأولى)",
        r"\bforget\s+(جميع\s+|كل\s+)?(تعليماتك|قواعدك)",
    ),
    "hi": (
        r"(पिछले|पिछली|पहले\s+के|ऊपर\s+के)\s+(सभी\s+|सारे\s+|all\s+)?"
        r"(निर्देशों|निर्देश|आदेशों|instructions?|rules|prompts?)\s+(को\s+)?"
        r"(अनदेखा|नज़रअंदाज़|नजरअंदाज|भूल|(ignore|forget|disregard)\s*कर)",
        r"(अपने|अपना|अपनी)\s+(सभी\s+)?(निर्देशों|नियमों|instructions?|rules)\s+को\s+"
        r"(भूल|(ignore|forget|disregard)\s*कर)",
        r"अब\s+(से\s+)?(तुम|आप)\s+(DAN|एक\s+(अलग|नया|अनियंत्रित))",
        r"(अपना|अपने|अपनी)\s+(सिस्टम\s+प्रॉम्प्ट|निर्देश|प्रॉम्प्ट|system\s+prompt|"
        r"prompt|instructions?)\s+(दिखाओ|दिखाएं|दिखाइए|बताओ|बताएं|प्रकट\s+करें|"
        r"(show|reveal)\s*कर)",
        r"सिस्टम\s+प्रॉम्प्ट\s+(दिखाओ|दिखाएं|दिखाइए|बताओ|बताएं)",
        # English-led
        r"\b(previous|prior|above|earlier)\s+(all\s+)?(instructions?|rules|prompts?)"
        r"\s+(को\s+)?(अनदेखा|नज़रअंदाज़|नजरअंदाज|भूल|(ignore|forget|disregard)\s*कर)",
    ),
}


#: Languages written in a non-Latin script.
NON_LATIN_LANGUAGES: frozenset[str] = frozenset({"ru", "zh", "ja", "ar", "hi"})

#: Languages whose script the ``deobfuscated`` views' confusables fold rewrites
#: (Cyrillic/Greek to Latin). Their patterns would never match there, so the
#: guard skips them on those views and runs them on the ``native`` views
#: instead, which collapse letter spacing without the fold. CJK, Arabic and
#: Devanagari survive the fold, and ``deobfuscated`` undoes their letter
#: spacing (``忽 略 之 前 …``): their patterns must run there.
FOLDED_SCRIPT_LANGUAGES: frozenset[str] = frozenset({"ru"})

# Whitespace and word-boundary tokens: what a whole-phrase (squashed) form of
# a pattern drops.
_SPACING_TOKEN_RE = re.compile(r"\\s[+*]?|\\b")

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

#: Expanded pattern -> the source it was expanded from, so a match is reported
#: under the pattern as written, not under its lookalike-class form.
_DISPLAY_SOURCES: dict[re.Pattern[str], str] = {}


def expand_cyrillic_lookalikes(source: str) -> str:
    """Let every Cyrillic letter of a pattern also match its Latin lookalikes.

    ``все`` becomes ``[вb][сc][еeёë]``, so ``Bce`` — Latin letters only —
    matches, as do mixed tokens (``npeдыдущue``) and leet digits (``3абудь``).
    Applied to the pattern rather than the text, so no extra view is
    scanned, and an English word can only match if it spells a whole
    Russian override phrase letter by letter. Adds character classes and
    bounded alternations only: no new quantifier, so the pattern stays
    linear.

    Args:
        source: Regex source whose Cyrillic letters are all literals.

    Returns:
        The pattern with each Cyrillic letter widened to its lookalikes.

    Raises:
        ValueError: The source has a character class, where a widened
            letter would no longer be a literal.
    """
    if "[" in source:
        raise ValueError(f"cannot expand a pattern with a class: {source!r}")
    parts: list[str] = []
    for ch in source:
        if ch in _CYRILLIC_LOOKALIKES:
            parts.append(f"[{ch}{_CYRILLIC_LOOKALIKES[ch]}]")
        elif ch in _CYRILLIC_LOOKALIKE_SEQUENCES:
            parts.append(f"(?:{'|'.join((ch, *_CYRILLIC_LOOKALIKE_SEQUENCES[ch]))})")
        else:
            parts.append(ch)
    return "".join(parts)


def pattern_source(pattern: re.Pattern[str]) -> str:
    """The source a compiled multilingual pattern is reported under.

    Args:
        pattern: A pattern compiled by :func:`compile_multilingual`.

    Returns:
        The source as written in the table (before any lookalike
        expansion).
    """
    return _DISPLAY_SOURCES.get(pattern, pattern.pattern)


def compile_multilingual(
    table: dict[str, tuple[str, ...]],
    *,
    lookalike_languages: frozenset[str] = frozenset(),
    lowercase: bool = False,
) -> list[tuple[str, re.Pattern[str]]]:
    """Compile a language table into ``(language, pattern)`` pairs.

    Patterns are NFKC-normalised before compiling so they line up with the
    guard's normalised text variant (NFKC decomposes some precomposed
    characters, e.g. Devanagari nukta letters).

    Args:
        table: Language code -> regex source strings.
        lookalike_languages: Languages whose patterns are widened with
            :func:`expand_cyrillic_lookalikes`; :func:`pattern_source` maps
            each back to its source.
        lowercase: The patterns only ever see lowercased text (the squashed
            views): lowercase the sources and compile case-sensitively, which
            lets the regex engine skip ahead to the first letter.

    Returns:
        Compiled patterns tagged with their language; case-insensitive
        unless ``lowercase``.
    """
    flags = 0 if lowercase else re.IGNORECASE
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for lang, sources in table.items():
        for source in sources:
            written = unicodedata.normalize("NFKC", source)
            if lowercase:
                if re.search(r"\\[A-Z]", written):
                    raise ValueError(f"cannot lowercase an escape in {written!r}")
                written = written.lower()
            if lang not in lookalike_languages:
                compiled.append((lang, re.compile(written, flags)))
                continue
            pattern = re.compile(expand_cyrillic_lookalikes(written), flags)
            _DISPLAY_SOURCES[pattern] = written
            compiled.append((lang, pattern))
    return compiled


def squash_pattern(source: str) -> str:
    r"""Whole-phrase form of a pattern, for letter-only (squashed) views.

    Drops every whitespace (``\s``, ``\s+``, ``\s*``) and word-boundary
    (``\b``) token, so ``забудь(те)?\s+(все\s+)?правила`` becomes
    ``забудь(те)?(все)?правила`` — what that phrase reads like once
    ``з а б у д ь в с е п р а в и л а`` has lost every gap.

    Args:
        source: Regex source of a word pattern.

    Returns:
        The same pattern with the spacing tokens removed.
    """
    return _SPACING_TOKEN_RE.sub("", source)


#: Pre-compiled multilingual patterns, tagged with their language code. The
#: folded-script (Russian) ones also match Latin lookalike spellings.
COMPILED_MULTILINGUAL_PATTERNS = compile_multilingual(
    MULTILINGUAL_INJECTION_PATTERNS, lookalike_languages=FOLDED_SCRIPT_LANGUAGES
)


def _squashed_table(*, non_latin: bool) -> dict[str, tuple[str, ...]]:
    """Whole-phrase forms of the patterns that rely on whitespace."""
    return {
        lang: tuple(squash_pattern(p) for p in sources if r"\s" in p)
        for lang, sources in MULTILINGUAL_INJECTION_PATTERNS.items()
        if (lang in NON_LATIN_LANGUAGES) is non_latin
    }


#: Whole-phrase forms of the non-Latin patterns that rely on whitespace, for
#: the ``native-squashed`` view. The others carry no whitespace (CJK, except
#: around an embedded English phrase), so the collapsed views serve them.
COMPILED_SQUASHED_MULTILINGUAL_PATTERNS = compile_multilingual(
    _squashed_table(non_latin=True),
    lookalike_languages=FOLDED_SCRIPT_LANGUAGES,
    lowercase=True,
)

#: Whole-phrase forms of the Latin-script languages' patterns, for the
#: ``squashed`` view: ``i g n o r a  t u t t e  l e  i s t r u z i o n i``
#: leaves the two-letter ``l e`` spaced (a run needs three letters), which
#: only the gapless form reads past.
COMPILED_SQUASHED_LATIN_MULTILINGUAL_PATTERNS = compile_multilingual(
    _squashed_table(non_latin=False), lowercase=True
)

# A pattern source whose first literal is a Latin letter, past any opening
# groups, ``\b`` or ``(?<![a-z])``.
_LATIN_LEAD_RE = re.compile(r"(?:\(\?<!\[a-z\]\)|\\b|\()*[a-z]")

#: Patterns of the script-bound languages (CJK, Arabic, Devanagari) that lead
#: with an English word: they cannot use the regex engine's first-character
#: scan, and cannot match a view without a Latin word, so the guard skips
#: them there. Russian is excluded — every one of its patterns can match
#: Latin lookalikes.
LATIN_LED_PATTERNS: frozenset[re.Pattern[str]] = frozenset(
    pattern
    for lang, pattern in (
        *COMPILED_MULTILINGUAL_PATTERNS,
        *COMPILED_SQUASHED_MULTILINGUAL_PATTERNS,
    )
    if lang in NON_LATIN_LANGUAGES - FOLDED_SCRIPT_LANGUAGES
    and _LATIN_LEAD_RE.match(pattern_source(pattern))
)

__all__ = [
    "COMPILED_MULTILINGUAL_PATTERNS",
    "COMPILED_SQUASHED_LATIN_MULTILINGUAL_PATTERNS",
    "COMPILED_SQUASHED_MULTILINGUAL_PATTERNS",
    "FOLDED_SCRIPT_LANGUAGES",
    "LATIN_LED_PATTERNS",
    "MULTILINGUAL_INJECTION_PATTERNS",
    "NON_LATIN_LANGUAGES",
    "compile_multilingual",
    "expand_cyrillic_lookalikes",
    "pattern_source",
    "squash_pattern",
]
