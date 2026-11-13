"""Cache values stay plain JSON whichever encoder wrote them."""

from __future__ import annotations

import json
import math

import pytest

from baselith.core.optimization._cache_json import _dumps, _loads


class TestCacheJson:
    @pytest.mark.parametrize(
        "value",
        [
            [0.1, -2.5e-9, 3.0],
            {"a": [1, 2], "b": {"c": None, "d": True}},
            "text",
            42,
            1.5,
            {"unicode": "caffè 日本"},
        ],
    )
    def test_either_decoder_reads_what_was_written(self, value: object) -> None:
        text = _dumps(value)

        assert _loads(text) == value
        assert json.loads(text) == value

    def test_entries_written_by_the_stdlib_are_still_read(self) -> None:
        legacy = json.dumps({"vector": [0.1, 0.2], "k": 1})

        assert _loads(legacy) == {"vector": [0.1, 0.2], "k": 1}

    def test_what_orjson_refuses_falls_back_to_the_stdlib(self) -> None:
        assert _loads(_dumps({1: "int key"})) == {"1": "int key"}
        assert _loads(_dumps(2**70)) == 2**70

    def test_a_stdlib_nan_is_still_parsed(self) -> None:
        assert math.isnan(_loads("NaN"))

    def test_non_json_raises_the_error_callers_catch(self) -> None:
        with pytest.raises(json.JSONDecodeError):
            _loads("not json")
