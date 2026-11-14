"""Payload equality predicates: index-friendly for string keys, unchanged otherwise."""

from __future__ import annotations

import pytest

from baselith.core.services.vectorstore.providers._pgvector_match import match_fragment


class TestMatchFragment:
    @pytest.mark.parametrize("key", ["tenant_id", "document_id"])
    def test_a_string_key_uses_containment_for_one_value(self, key: str) -> None:
        params: list = []

        sql = match_fragment(key, "v1", params, negate=False)

        assert sql == "payload @> %s::jsonb"
        assert params == [f'{{"{key}":"v1"}}']

    def test_a_list_becomes_any_over_a_jsonb_array(self) -> None:
        params: list = []

        sql = match_fragment("document_id", ["a", "b"], params, negate=False)

        assert sql == "payload @> ANY(%s::jsonb[])"
        assert params == [['{"document_id":"a"}', '{"document_id":"b"}']]

    def test_set_and_tuple_values_are_lists_too(self) -> None:
        for values in ({"a"}, ("a",), frozenset({"a"})):
            params: list = []
            sql = match_fragment("document_id", values, params, negate=False)
            assert "ANY" in sql and params == [['{"document_id":"a"}']]

    def test_values_are_json_escaped(self) -> None:
        params: list = []

        match_fragment("document_id", 'we"ird\\id', params, negate=False)

        assert params == ['{"document_id":"we\\"ird\\\\id"}']

    def test_other_keys_keep_the_text_form(self) -> None:
        """Their stored type is not ours to assume: ``->>`` also matches numbers."""
        one: list = []
        many: list = []

        assert match_fragment("lang", "fr", one, negate=False) == "payload->>%s = %s"
        assert one == ["lang", "fr"]
        assert (
            match_fragment("lang", ["fr", "it"], many, negate=False)
            == "payload->>%s = ANY(%s)"
        )
        assert many == ["lang", ["fr", "it"]]

    def test_a_negated_condition_keeps_the_text_form(self) -> None:
        """``NOT (payload @> x)`` is true for a row without the key; ``NOT
        (payload->>k = v)`` is NULL and drops it. The meaning must not move."""
        params: list = []

        sql = match_fragment("document_id", "d1", params, negate=True)

        assert sql == "payload->>%s = %s"
        assert params == ["document_id", "d1"]

    def test_an_empty_list_matches_nothing_in_either_form(self) -> None:
        params: list = []

        assert match_fragment("document_id", [], params, negate=False).endswith(
            "::jsonb[])"
        )
        assert params == [[]]
