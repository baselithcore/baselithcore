"""Unicode canonicalization shared by keyword indexing, dedup and cache keys."""

from core.utils.text_canon import canonical_key, canonicalize


class TestCanonicalize:
    def test_strips_accents(self) -> None:
        assert canonicalize("perché città") == "perche citta"

    def test_nfkc_folds_compatibility_forms(self) -> None:
        # Ligature and fullwidth letters collapse to their ASCII equivalents.
        assert canonicalize("ﬁle Ｐython") == "file python"

    def test_casefolds(self) -> None:
        assert canonicalize("Straße") == "strasse"

    def test_collapses_whitespace(self) -> None:
        assert canonicalize("  what \t is\n python ") == "what is python"

    def test_keeps_punctuation(self) -> None:
        assert canonicalize("What is Python?") == "what is python?"

    def test_recomposes_hangul(self) -> None:
        # NFD splits Hangul into jamo; the result must be recomposed so a
        # canonical string compares equal to the same text typed normally.
        assert canonicalize("한국어") == "한국어"

    def test_empty(self) -> None:
        assert canonicalize("") == ""


class TestCanonicalKey:
    def test_drops_punctuation(self) -> None:
        assert canonical_key("What is Python?") == "what is python"

    def test_punctuation_variants_share_key(self) -> None:
        assert canonical_key("Café aperto!") == canonical_key("cafe  aperto")

    def test_keeps_word_boundaries(self) -> None:
        # Punctuation becomes a separator, never a glue: "v1.2" != "v12".
        assert canonical_key("v1.2") == "v1 2"
        assert canonical_key("v1.2") != canonical_key("v12")

    def test_preserves_cjk(self) -> None:
        assert canonical_key("東京タワー") == "東京タワー"
