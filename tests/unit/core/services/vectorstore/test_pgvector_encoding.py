"""The fast vector encoder must agree with the per-float one it replaced."""

from __future__ import annotations

import random
from collections.abc import Sequence
from typing import Any

import numpy as np
import pytest

from baselith.core.services.vectorstore.providers._pgvector_encoding import (
    _encode_vector,
)


def _reference(vector: Sequence[Any]) -> str:
    return "[" + ",".join(repr(float(v)) for v in vector) + "]"


def _values(text: str) -> list[float]:
    return [float(part) for part in text[1:-1].split(",") if part]


class TestEncodeVector:
    def test_a_float_list_round_trips_exactly(self) -> None:
        rnd = random.Random(7)
        vector = [rnd.uniform(-1, 1) for _ in range(1024)]

        assert _values(_encode_vector(vector)) == vector

    @pytest.mark.parametrize(
        "vector",
        [
            [],
            [1, 2, 3],
            [0.1, 1e-7, 1e22, -0.0, 5e-324],
            (0.5, 1.0),
            [np.float32(0.5), 1.0],
            np.array([0.5, 0.25], dtype=np.float32),
        ],
    )
    def test_same_numbers_as_the_reference(self, vector: Sequence[Any]) -> None:
        assert _values(_encode_vector(vector)) == _values(_reference(vector))

    @pytest.mark.parametrize(
        "vector", [[float("nan"), 1.0], [float("inf")], [True, 2.0], ["1.0"]]
    )
    def test_what_json_cannot_say_takes_the_float_path(
        self, vector: Sequence[Any]
    ) -> None:
        """``null``, ``true`` and a quoted number are not pgvector literals."""
        assert _encode_vector(vector) == _reference(vector)

    def test_the_output_is_only_brackets_and_numbers(self) -> None:
        encoded = _encode_vector([0.25, -1.5e-9, 3])

        assert encoded.startswith("[") and encoded.endswith("]")
        assert not set(encoded) - set("0123456789eE.+-,[]")
