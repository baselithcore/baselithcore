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
        r"игнорируй(те)?\s+(все\s+)?(предыдущие|прежние|вышеуказанные)\s+"
        r"(инструкции|указания)",
        r"забудь(те)?\s+(все\s+)?(свои\s+|твои\s+|ваши\s+|предыдущие\s+)?"
        r"(инструкции|правила|указания)",
        r"не\s+обращай(те)?\s+внимания\s+на\s+(все\s+)?(предыдущие|прежние)\s+"
        r"(инструкции|указания)",
        r"(теперь\s+ты|ты\s+теперь)\s+(dan|другой|новый|свободный|"
        r"неограниченный|без\s+ограничений)",
        r"(покажи|раскрой|выведи|повтори)(те)?\s+(мне\s+)?"
        r"(свой|свои|твой|твои|ваш|ваши)\s+(системный\s+)?(промпт|инструкции)",
        r"(покажи|раскрой|выведи)(те)?\s+(мне\s+)?системный\s+промпт",
    ),
    "zh": (
        r"(忽略|无视|無視|忽視|不要理会|不要理會)[^。！？\n]{0,8}?"
        r"(之前|以前|先前|上面|上述|前面)[^。！？\n]{0,6}?"
        r"(指令|指示|提示|说明|說明|规则|規則)",
        r"忘(记|記|掉)[^。！？\n]{0,6}?(你的|所有)[^。！？\n]{0,4}?"
        r"(指令|规则|規則|设定|設定)",
        r"(你现在是|你現在是|从现在开始你是|從現在開始你是)"
        r"(一个|一個|一名|DAN)",
        r"(显示|顯示|输出|輸出|告诉我|告訴我|泄露|洩露|打印|重复|重複)"
        r"[^。！？\n]{0,6}?(系统提示|系統提示|初始指令)",
        r"(显示|顯示|输出|輸出|告诉我|告訴我|泄露|洩露)[^。！？\n]{0,4}?"
        r"你的(指令|提示词|提示詞|指示)",
    ),
    "ja": (
        r"(以前|前|上記|これまで)の[^。！？\n]{0,6}?(指示|命令|指令|プロンプト)を"
        r"[^。！？\n]{0,4}?(無視|忘れ)",
        r"(あなた|君|お前)は(今|今から|これから)は?"
        r"(DAN|制限のない|自由な|別の)",
        r"システムプロンプトを[^。！？\n]{0,6}?"
        r"(表示|教えて|出力|見せて|明かして)",
        r"(あなたの|君の)(指示|プロンプト|命令)を[^。！？\n]{0,4}?"
        r"(表示|教えて|出力|見せて|明かして)",
    ),
    "ar": (
        r"تجاهل\s+(جميع\s+|كل\s+)?(التعليمات|الأوامر|الإرشادات)\s+"
        r"(السابقة|الأولى)",
        r"انس\s+(جميع\s+|كل\s+)?(تعليماتك|قواعدك)",
        r"لا\s+تلتزم\s+ب(التعليمات|الأوامر)\s+السابقة",
        r"أنت\s+الآن\s+(DAN|نموذج|مساعد\s+بدون|بدون\s+قيود)",
        r"(اكشف|أظهر|اعرض|اطبع|كرر)\s+(لي\s+)?"
        r"(تعليماتك|موجه\s+النظام|التعليمات\s+الأصلية)",
    ),
    "hi": (
        r"(पिछले|पिछली|पहले\s+के|ऊपर\s+के)\s+(सभी\s+|सारे\s+)?"
        r"(निर्देशों|निर्देश|आदेशों)\s+(को\s+)?(अनदेखा|नज़रअंदाज़|नजरअंदाज|भूल)",
        r"(अपने|अपना|अपनी)\s+(सभी\s+)?(निर्देशों|नियमों)\s+को\s+भूल",
        r"अब\s+(से\s+)?(तुम|आप)\s+(DAN|एक\s+(अलग|नया|अनियंत्रित))",
        r"(अपना|अपने|अपनी)\s+(सिस्टम\s+प्रॉम्प्ट|निर्देश|प्रॉम्प्ट)\s+"
        r"(दिखाओ|दिखाएं|दिखाइए|बताओ|बताएं|प्रकट\s+करें)",
        r"सिस्टम\s+प्रॉम्प्ट\s+(दिखाओ|दिखाएं|दिखाइए|बताओ|बताएं)",
    ),
}


#: Languages written in a non-Latin script. Their patterns are skipped on the
#: ``deobfuscated`` views, whose confusables fold rewrites Cyrillic/Greek
#: letters to Latin — they would never match there, and skipping them keeps
#: the scan cost down.
NON_LATIN_LANGUAGES: frozenset[str] = frozenset({"ru", "zh", "ja", "ar", "hi"})


def compile_multilingual(
    table: dict[str, tuple[str, ...]],
) -> list[tuple[str, re.Pattern[str]]]:
    """Compile a language table into ``(language, pattern)`` pairs.

    Patterns are NFKC-normalised before compiling so they line up with the
    guard's normalised text variant (NFKC decomposes some precomposed
    characters, e.g. Devanagari nukta letters).

    Args:
        table: Language code -> regex source strings.

    Returns:
        Compiled, case-insensitive patterns tagged with their language.
    """
    return [
        (lang, re.compile(unicodedata.normalize("NFKC", source), re.IGNORECASE))
        for lang, sources in table.items()
        for source in sources
    ]


#: Pre-compiled multilingual patterns, tagged with their language code.
COMPILED_MULTILINGUAL_PATTERNS = compile_multilingual(MULTILINGUAL_INJECTION_PATTERNS)

__all__ = [
    "COMPILED_MULTILINGUAL_PATTERNS",
    "MULTILINGUAL_INJECTION_PATTERNS",
    "NON_LATIN_LANGUAGES",
    "compile_multilingual",
]
