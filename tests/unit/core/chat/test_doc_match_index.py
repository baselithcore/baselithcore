"""Inverted doc-match index: exact equivalence with the linear scan."""

import random
import string

import pytest

import core.chat.mixins._doc_match_index as index_module
from core.chat.mixins._doc_match_index import get_doc_match_index
from core.chat.mixins.retrieval_search import _doc_match_fields


def _items(entries):
    return {
        doc_id: {"metadata": {"title": t, "filename": f, "relative_path": r}}
        for doc_id, (t, f, r) in entries.items()
    }


def _naive_match(indexed_items, query_l, tokens):
    """The original per-document scan, verbatim semantics."""
    out = []
    for doc_id, meta in indexed_items.items():
        md = meta.get("metadata") or {}
        title, filename, rel, stems = _doc_match_fields(doc_id, md)
        if (
            (title and title in query_l)
            or (filename and filename in query_l)
            or (rel and rel in query_l)
            or any(stem in tokens for stem in stems)
        ):
            out.append(doc_id)
    return out


def _tokens(query_l):
    return set(query_l.replace("_", " ").replace("-", " ").split())


@pytest.fixture(autouse=True)
def _reset_index(monkeypatch):
    monkeypatch.setattr(index_module, "_index", None)


def _assert_equivalent(items, query):
    query_l = query.lower()
    tokens = _tokens(query_l)
    index = get_doc_match_index(items, _doc_match_fields)
    assert index.match(query_l, tokens) == _naive_match(items, query_l, tokens)


def test_title_substring_match():
    items = _items({"d1": ("Quarterly Report", "q.pdf", "docs/q.pdf")})
    _assert_equivalent(items, "open the quarterly report please")


def test_midword_substring_still_matches():
    # The original scan matches 'port' inside 'important' — semantics kept.
    items = _items({"d1": ("port", "", "")})
    _assert_equivalent(items, "this is important")
    index = get_doc_match_index(items, _doc_match_fields)
    assert index.match("this is important", _tokens("this is important")) == ["d1"]


def test_stem_token_match():
    items = _items({"d1": ("", "budget_2026.xlsx", "")})
    _assert_equivalent(items, "show me budget_2026 numbers")


def test_no_match():
    items = _items({"d1": ("Alpha", "a.md", "x/a.md")})
    _assert_equivalent(items, "completely unrelated query")


def test_order_preserved_and_multiple_docs():
    items = _items(
        {
            "d1": ("alpha report", "", ""),
            "d2": ("beta report", "", ""),
            "d3": ("alpha beta", "", ""),
        }
    )
    query = "alpha report and beta report and alpha beta"
    _assert_equivalent(items, query)
    index = get_doc_match_index(items, _doc_match_fields)
    assert index.match(query.lower(), _tokens(query)) == ["d1", "d2", "d3"]


def test_rebuild_only_on_corpus_change():
    items = _items({"d1": ("alpha", "", "")})
    first = get_doc_match_index(items, _doc_match_fields)
    again = get_doc_match_index(items, _doc_match_fields)
    assert again is first  # cached

    items2 = _items({"d1": ("alpha", "", ""), "d2": ("gamma", "", "")})
    rebuilt = get_doc_match_index(items2, _doc_match_fields)
    assert rebuilt is not first
    assert rebuilt.match("gamma ray", {"gamma", "ray"}) == ["d2"]


def test_versioned_probe_skips_snapshot_compare():
    """With a `version` token the cache decision is O(1): same token → same
    index object back, no per-document snapshot walk; a bumped token forces
    the rebuild even for an in-place mutation of the same dict."""
    items = _items({"d1": ("alpha", "", "")})
    first = get_doc_match_index(items, _doc_match_fields, version=7)
    assert get_doc_match_index(items, _doc_match_fields, version=7) is first

    # The len guard catches an unsignalled in-place insertion even when the
    # owner forgot to bump the counter — belt and braces, still O(1).
    items["d2"] = {"metadata": {"title": "gamma", "filename": "", "relative_path": ""}}
    guarded = get_doc_match_index(items, _doc_match_fields, version=7)
    assert guarded is not first
    assert guarded.match("gamma ray", {"gamma", "ray"}) == ["d2"]

    # A bumped version alone (same dict, same len) also forces the rebuild:
    # this is the owner's signal for same-size content mutations.
    items["d2"]["metadata"]["title"] = "delta"
    rebuilt = get_doc_match_index(items, _doc_match_fields, version=8)
    assert rebuilt is not first
    assert rebuilt.match("delta wing", {"delta", "wing"}) == ["d2"]


def test_versioned_probe_rebuilds_when_registry_object_changes():
    """A reload replaces the whole dict: same version token but a different
    container must rebuild (version counters reset across restarts)."""
    items = _items({"d1": ("alpha", "", "")})
    first = get_doc_match_index(items, _doc_match_fields, version=3)
    replacement = _items({"d9": ("omega", "", "")})
    rebuilt = get_doc_match_index(replacement, _doc_match_fields, version=3)
    assert rebuilt is not first
    assert rebuilt.match("omega point", {"omega", "point"}) == ["d9"]


def test_fuzz_equivalence_against_naive_scan():
    rng = random.Random(42)
    words = ["report", "budget", "alpha", "beta", "plan", "notes", "q1", "x"]

    def rand_field():
        if rng.random() < 0.3:
            return ""
        return " ".join(rng.sample(words, rng.randint(1, 2)))

    for _ in range(30):
        entries = {
            f"d{i}": (rand_field(), rand_field(), rand_field())
            for i in range(rng.randint(1, 12))
        }
        items = _items(entries)
        query = " ".join(
            rng.choices(
                words + ["".join(rng.choices(string.ascii_lowercase, k=5))], k=6
            )
        )
        index_module._index = None  # force rebuild per corpus
        _assert_equivalent(items, query)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
