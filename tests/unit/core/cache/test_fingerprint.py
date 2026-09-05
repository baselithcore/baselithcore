"""Hashed n-gram fingerprints: the O(1)-ish static lookup tier of the
semantic cache — catches near-verbatim prompt variants without an embedder."""

from core.cache.fingerprint import best_fingerprint_match, jaccard, ngram_fingerprint


class TestNgramFingerprint:
    def test_contains_unigrams_and_bigrams(self) -> None:
        assert ngram_fingerprint("what is python") == frozenset(
            {"what", "is", "python", "what is", "is python"}
        )

    def test_canonicalizes_before_shingling(self) -> None:
        # Case, punctuation, accents and whitespace never change the fingerprint.
        assert ngram_fingerprint("Perché  Python?!") == ngram_fingerprint(
            "perche python"
        )

    def test_empty_text_yields_empty_fingerprint(self) -> None:
        assert ngram_fingerprint("") == frozenset()
        assert ngram_fingerprint("?!") == frozenset()


class TestJaccard:
    def test_identical_sets_score_one(self) -> None:
        fp = ngram_fingerprint("what is python")
        assert jaccard(fp, fp) == 1.0

    def test_disjoint_sets_score_zero(self) -> None:
        assert jaccard(frozenset({"a"}), frozenset({"b"})) == 0.0

    def test_both_empty_scores_zero(self) -> None:
        # Two empty prompts share nothing; never report them as a match.
        assert jaccard(frozenset(), frozenset()) == 0.0

    def test_reordering_is_penalized_by_bigrams(self) -> None:
        # Same words, opposite meaning: bigrams keep the score low.
        a = ngram_fingerprint("translate english to italian")
        b = ngram_fingerprint("translate italian to english")
        assert jaccard(a, b) < 0.5


class TestBestFingerprintMatch:
    def test_returns_best_candidate_above_threshold(self) -> None:
        query = ngram_fingerprint("what is python")
        candidates = [
            ("far", ngram_fingerprint("lazy dog naps")),
            ("near", ngram_fingerprint("what is python?")),
        ]
        assert best_fingerprint_match(query, candidates, threshold=0.8) == (
            "near",
            1.0,
        )

    def test_returns_none_below_threshold(self) -> None:
        query = ngram_fingerprint("what is python")
        candidates = [("far", ngram_fingerprint("lazy dog naps"))]
        assert best_fingerprint_match(query, candidates, threshold=0.8) is None

    def test_empty_candidates(self) -> None:
        assert best_fingerprint_match(frozenset({"a"}), [], threshold=0.5) is None
