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
a native word. Spelling variants are widened on the pattern side, at compile
time (:mod:`baselith.core.guardrails.variants`): Russian letters to their
Latin lookalikes, Arabic letters to their Arabizi digits and informal
spellings, Chinese keywords to curated homophones and variant characters.
The romanised families — ``ru-latn`` (the Russian table, transliterated),
``hi-latn`` and ``ar-latn`` (:mod:`baselith.core.guardrails.romanized`) —
cover the same intents typed in the Latin alphabet.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Mapping

from .romanized import ROMANIZED_INJECTION_PATTERNS
from .variants import (
    expand_arabic_variants,
    expand_cyrillic_lookalikes,
    expand_zh_homophones,
    transliterate_cyrillic,
)

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
        r"(忘记|忘記|忘掉)[^。！？\n]{0,6}?(你的|所有)[^。！？\n]{0,4}?"
        r"(指令|规则|規則|设定|設定|instructions?|rules)",
        r"(忽略|无视|無視|忽視|不要理会|不要理會|(忘记|忘記|忘掉))[^。！？\n]{0,8}?"
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
        # Kana spellings ride along: ``むし``/``ムシ`` (slang for ignoring),
        # ``しじ``, ``ぷろんぷと``, ``しすてむ``, ``ひょうじ``...
        r"(以前|いぜん|前|まえ|上記|これまで)の[^。！？\n]{0,6}?"
        r"(指示|しじ|命令|めいれい|指令|しれい|プロンプト|ぷろんぷと|"
        r"instructions?|rules|prompts?)を[^。！？\n]{0,4}?(無視|むし|ムシ|"
        r"忘れ|わすれ|(?<![a-z])(ignore|disregard|forget)(?![a-z]))",
        r"(あなた|君|きみ|お前|おまえ)は(今|いま|今から|いまから|これから)は?"
        r"(DAN|制限のない|せいげんのない|自由な|別の)",
        r"(システム|しすてむ)(プロンプト|ぷろんぷと)を[^。！？\n]{0,6}?"
        r"(表示|ひょうじ|教えて|おしえて|出力|見せて|みせて|明かして|あかして|"
        r"(?<![a-z])(show|reveal|print|output|display)(?![a-z]))",
        r"(あなたの|君の|きみの)(指示|しじ|プロンプト|ぷろんぷと|命令|めいれい)を"
        r"[^。！？\n]{0,4}?(表示|ひょうじ|教えて|おしえて|出力|見せて|みせて|明かして|"
        r"あかして)",
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
# Latin-alphabet forms. Russian typed in transliteration is the Russian table
# again, rewritten letter by letter at compile time (``PATTERN_EXPANDERS``).
MULTILINGUAL_INJECTION_PATTERNS |= {
    "ru-latn": MULTILINGUAL_INJECTION_PATTERNS["ru"],
    **ROMANIZED_INJECTION_PATTERNS,
}

#: Language -> rewrite applied to each of its pattern sources before
#: compiling (:mod:`baselith.core.guardrails.variants`); a match is still
#: reported under the source as written (:func:`pattern_source`).
PATTERN_EXPANDERS: Mapping[str, Callable[[str], str]] = {
    "ru": expand_cyrillic_lookalikes,
    "ru-latn": transliterate_cyrillic,
    "ar": expand_arabic_variants,
    "zh": expand_zh_homophones,
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

#: Expanded pattern -> the source it was expanded from, so a match is reported
#: under the pattern as written, not under its widened form.
_DISPLAY_SOURCES: dict[re.Pattern[str], str] = {}


def pattern_source(pattern: re.Pattern[str]) -> str:
    """The source a compiled multilingual pattern is reported under.

    Args:
        pattern: A pattern compiled by :func:`compile_multilingual`.

    Returns:
        The source as written in the table (before any variant
        expansion).
    """
    return _DISPLAY_SOURCES.get(pattern, pattern.pattern)


def compile_multilingual(
    table: dict[str, tuple[str, ...]],
    *,
    expanders: Mapping[str, Callable[[str], str]] | None = None,
    lowercase: bool = False,
) -> list[tuple[str, re.Pattern[str]]]:
    """Compile a language table into ``(language, pattern)`` pairs.

    Patterns are NFKC-normalised before compiling so they line up with the
    guard's normalised text variant (NFKC decomposes some precomposed
    characters, e.g. Devanagari nukta letters).

    Args:
        table: Language code -> regex source strings.
        expanders: Language -> source rewrite (see
            :data:`PATTERN_EXPANDERS`); :func:`pattern_source` maps each
            rewritten pattern back to its source.
        lowercase: The patterns only ever see lowercased text: lowercase
            the sources and compile case-sensitively, which lets the regex
            engine skip ahead to the first letter.

    Returns:
        Compiled patterns tagged with their language; case-insensitive
        unless ``lowercase``.
    """
    flags = 0 if lowercase else re.IGNORECASE
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for lang, sources in table.items():
        expand = (expanders or {}).get(lang)
        for source in sources:
            written = unicodedata.normalize("NFKC", source)
            body = written
            if lowercase:
                if re.search(r"\\[A-Z]", body):
                    raise ValueError(f"cannot lowercase an escape in {body!r}")
                body = body.lower()
            if expand is not None:
                body = expand(body)
            pattern = re.compile(body, flags)
            if body != written:
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


#: Pre-compiled multilingual patterns, tagged with their language code, each
#: widened by its language's expander. Compiled lowercase and case-sensitive,
#: and matched against the lowercased view (:data:`LOWERCASE_PATTERN_IDS`):
#: a case-insensitive pattern defeats the regex engine's skip-ahead to its
#: first literal, which made each one several times slower on plain text.
COMPILED_MULTILINGUAL_PATTERNS = compile_multilingual(
    MULTILINGUAL_INJECTION_PATTERNS, expanders=PATTERN_EXPANDERS, lowercase=True
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
    expanders=PATTERN_EXPANDERS,
    lowercase=True,
)

#: Whole-phrase forms of the Latin-script languages' patterns, for the
#: ``squashed`` view: ``i g n o r a  t u t t e  l e  i s t r u z i o n i``
#: leaves the two-letter ``l e`` spaced (a run needs three letters), which
#: only the gapless form reads past.
COMPILED_SQUASHED_LATIN_MULTILINGUAL_PATTERNS = compile_multilingual(
    _squashed_table(non_latin=False), expanders=PATTERN_EXPANDERS, lowercase=True
)

#: Identities of every pattern above: compiled lowercase, they are matched
#: against the lowercased view text (the squashed views already are).
LOWERCASE_PATTERN_IDS: frozenset[int] = frozenset(
    id(pattern)
    for _, pattern in (
        *COMPILED_MULTILINGUAL_PATTERNS,
        *COMPILED_SQUASHED_MULTILINGUAL_PATTERNS,
        *COMPILED_SQUASHED_LATIN_MULTILINGUAL_PATTERNS,
    )
)

# A pattern source whose first literal is a Latin letter, past any opening
# groups, ``\b`` or ``(?<![a-z])``.
_LATIN_LEAD_RE = re.compile(r"(?:\(\?<!\[a-z\]\)|\\b|\()*[a-z]")

#: Patterns that cannot match a view without a Latin word, so the guard skips
#: them there: every pattern of the Latin-script languages (romanised ones
#: included — each spells out at least one Latin word), and the patterns of
#: the script-bound languages (CJK, Arabic, Devanagari) that lead with an
#: English word, which also cannot use the regex engine's first-character
#: scan. Russian is excluded — every one of its patterns can match Latin
#: lookalikes.
LATIN_LED_PATTERNS: frozenset[re.Pattern[str]] = frozenset(
    pattern
    for lang, pattern in (
        *COMPILED_MULTILINGUAL_PATTERNS,
        *COMPILED_SQUASHED_MULTILINGUAL_PATTERNS,
        *COMPILED_SQUASHED_LATIN_MULTILINGUAL_PATTERNS,
    )
    if lang not in NON_LATIN_LANGUAGES
    or (
        lang not in FOLDED_SCRIPT_LANGUAGES
        and _LATIN_LEAD_RE.match(pattern_source(pattern)) is not None
    )
)

__all__ = [
    "COMPILED_MULTILINGUAL_PATTERNS",
    "COMPILED_SQUASHED_LATIN_MULTILINGUAL_PATTERNS",
    "COMPILED_SQUASHED_MULTILINGUAL_PATTERNS",
    "FOLDED_SCRIPT_LANGUAGES",
    "LATIN_LED_PATTERNS",
    "LOWERCASE_PATTERN_IDS",
    "MULTILINGUAL_INJECTION_PATTERNS",
    "NON_LATIN_LANGUAGES",
    "PATTERN_EXPANDERS",
    "compile_multilingual",
    "expand_cyrillic_lookalikes",
    "pattern_source",
    "squash_pattern",
]
