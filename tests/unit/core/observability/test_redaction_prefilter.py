"""The masking prefilter may skip a regex only when it could not have matched.

``_mask_text`` runs on the calling thread for every string of every log line,
so it skips the email, credential and token passes on cheap substring tests.
Each test is a *necessary* condition of the pass it guards, which makes the
filtered function equal to the unfiltered one on every input — the property
these tests pin, including the inputs where a naive lowercase test would not
equal ``re.IGNORECASE`` (``ſ``, ``ı`` and the Kelvin sign fold onto ASCII).
"""

from __future__ import annotations

import random

import pytest

from baselith.core.observability import redaction

_LOOKALIKES = ("paſſword", "apı_key", "ſecret", "ſeſſion", "toKen", "Key")
_TOKENS = (
    "eyJabcdefgh.abcdefgh.sig",
    "ya29.abcdefghijklmn",
    "1//abcdefghijklmnopqrstuvwxyz",
)
_GLUE = (
    "=",
    "= ",
    ": ",
    '": "',
    "'",
    '"',
    " ",
    "\n",
    "{",
    "}",
    "&",
    ";",
    ",",
    "_",
    "-",
)
_WORDS = (
    "hunter2",
    "p@ss",
    "a@b.co",
    "john.smith@example.com",
    "Bearer abcdefgh12345",
    "bearer   x.y.z-123456",
    "code=1234",
    "tokenizer",
    "max_tokens",
    "secretary",
    "user",
    "é",
    "日本",
    "Hash",
)


def _fragments() -> list[str]:
    keywords = list(redaction._CREDENTIAL_KEYWORDS)
    return [
        *keywords,
        *(k.upper() for k in keywords),
        *(k.title() for k in keywords),
        *_LOOKALIKES,
        *_TOKENS,
        *_GLUE,
        *_WORDS,
    ]


class TestPrefilterEquivalence:
    def test_filtered_equals_unfiltered_on_a_generated_corpus(self) -> None:
        rnd = random.Random(20261113)
        fragments = _fragments()

        for _ in range(20_000):
            text = "".join(rnd.choice(fragments) for _ in range(rnd.randint(1, 9)))
            assert redaction._mask_text(text) == redaction._mask_text_unfiltered(
                text
            ), text

    @pytest.mark.parametrize(
        "text",
        [
            "PASSWORD=hunter2",
            "Authorization: Bearer abcdefgh12345",
            "contact john.smith@example.com now",
            "id_token eyJabcdefgh.abcdefgh.sig",
            "refresh 1//abcdefghijklmnopqrstuvwxyz",
            "access ya29.abcdefghijklmn",
            "paſſword=hunter2",
            "apı_key=abc123",
        ],
    )
    def test_secrets_are_still_masked(self, text: str) -> None:
        masked = redaction._mask_text(text)

        assert masked != text
        assert masked == redaction._mask_text_unfiltered(text)

    def test_plain_text_is_returned_untouched(self) -> None:
        text = "Processing request for tenant default took 12.5 ms"

        assert redaction._mask_text(text) is text

    def test_every_keyword_is_reachable_through_a_needle(self) -> None:
        """A keyword no needle covers would be skipped by the prefilter."""
        for keyword in redaction._CREDENTIAL_KEYWORDS:
            assert any(n in keyword for n in redaction._CREDENTIAL_NEEDLES), keyword

    def test_every_token_alternative_has_its_prefix(self) -> None:
        """A new ``_JWT`` alternative needs its literal in ``_TOKEN_PREFIXES``."""
        alternatives = redaction._JWT.pattern.split("|")

        assert len(alternatives) == len(redaction._TOKEN_PREFIXES)
        for token in _TOKENS:
            assert any(p in token for p in redaction._TOKEN_PREFIXES)
            assert redaction._mask_text(f"x {token}") == f"x {redaction._REDACTED}"
