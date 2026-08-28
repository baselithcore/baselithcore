"""State-copy semantics of :class:`InMemoryCheckpointStore`.

The store copies checkpoint state on every ``save`` and ``load`` — i.e. once
per agent step — so that path is optimized away from ``copy.deepcopy`` onto a
specialized walk over the JSON container types. These tests pin the contract
that walk must not break: **the result is indistinguishable from a plain
``copy.deepcopy``**, both in value and in type, and the store never shares a
mutable object with its callers.

The types below are exactly the ones a naive ``orjson`` round-trip would
silently rewrite (tuple to list, ``datetime``/``UUID`` to string, NaN to
``None``, int dict keys to strings) — which is why the fast path falls back to
``deepcopy`` for them instead.
"""

from __future__ import annotations

import copy
import datetime
import enum
import math
import uuid
from typing import Any

import pytest

from core.orchestration.checkpoint import Checkpoint
from core.orchestration.checkpoint_memory import (
    InMemoryCheckpointStore,
    _copy_state,
)


class Flavour(str, enum.Enum):
    SWEET = "sweet"


def _exotic_state() -> dict[str, Any]:
    """Values whose JSON encoding is lossy, plus ordinary JSON data."""
    return {
        "tuple": (1, "two", (3.0,)),
        "datetime": datetime.datetime(2024, 5, 17, 12, 30, 1),
        "uuid": uuid.UUID("12345678-1234-5678-1234-567812345678"),
        "set": {1, 2, 3},
        "bytes": b"\x00binary",
        "nan": float("nan"),
        "inf": float("inf"),
        "int_keys": {1: "one", 2: "two"},
        "enum": Flavour.SWEET,
        "big_int": 2**70,
        "nested": {"list": [{"deep": ("t",)}], "plain": [1, 2.5, "s", True, None]},
    }


def _assert_faithful(original: Any, copied: Any, path: str = "$") -> None:
    """Same type and same value everywhere, NaN included."""
    assert type(original) is type(copied), f"{path}: type changed"
    if isinstance(original, float) and math.isnan(original):
        assert math.isnan(copied), f"{path}: NaN lost"
        return
    if isinstance(original, dict):
        assert list(original.keys()) == list(copied.keys()), f"{path}: keys changed"
        for key in original:
            _assert_faithful(original[key], copied[key], f"{path}.{key}")
        return
    if isinstance(original, list | tuple):
        assert len(original) == len(copied), f"{path}: length changed"
        for i, (a, b) in enumerate(zip(original, copied, strict=True)):
            _assert_faithful(a, b, f"{path}[{i}]")
        return
    assert original == copied, f"{path}: value changed"


def _assert_no_shared_containers(original: Any, copied: Any, path: str = "$") -> None:
    """No mutable container may be shared between source and copy."""
    if isinstance(original, dict | list | set):
        assert original is not copied, f"{path}: container aliased"
    if isinstance(original, dict):
        for key in original:
            _assert_no_shared_containers(original[key], copied[key], f"{path}.{key}")
    elif isinstance(original, list | tuple):
        for i, (a, b) in enumerate(zip(original, copied, strict=True)):
            _assert_no_shared_containers(a, b, f"{path}[{i}]")


class TestCopyState:
    def test_matches_deepcopy_on_plain_json_state(self) -> None:
        state = {
            "run_id": "r1",
            "step": 3,
            "budget": {"iterations": 2, "cost_usd": 0.5},
            "trajectory": [{"cursor": 0, "tool": "search", "args": {"q": "x"}}],
            "answer": None,
            "flags": [True, False],
        }
        copied = _copy_state(state)
        _assert_faithful(copy.deepcopy(state), copied)
        _assert_no_shared_containers(state, copied)

    def test_preserves_types_a_json_round_trip_would_destroy(self) -> None:
        state = _exotic_state()
        copied = _copy_state(state)
        _assert_faithful(copy.deepcopy(state), copied)
        _assert_no_shared_containers(state, copied)
        # Spelled out, because these are the silent-corruption cases.
        assert isinstance(copied["tuple"], tuple)
        assert isinstance(copied["datetime"], datetime.datetime)
        assert isinstance(copied["uuid"], uuid.UUID)
        assert isinstance(copied["set"], set)
        assert isinstance(copied["bytes"], bytes)
        assert math.isnan(copied["nan"])
        assert copied["inf"] == float("inf")
        assert list(copied["int_keys"]) == [1, 2]
        assert copied["enum"] is Flavour.SWEET
        assert copied["big_int"] == 2**70

    def test_silently_lossy_types_are_not_coerced(self) -> None:
        """The dangerous half: values ``orjson`` *encodes* rather than rejects.

        Nothing here raises on ``orjson.dumps``, so a round-trip guarded only
        by ``try/except TypeError`` would sail through and quietly hand back
        lists, strings and ``None`` in place of the original state.
        """
        state = {
            "tuple": ("a", "b"),
            "datetime": datetime.datetime(2024, 1, 1),
            "uuid": uuid.UUID("12345678-1234-5678-1234-567812345678"),
            "nan": float("nan"),
            "inf": float("-inf"),
            "enum": Flavour.SWEET,
        }
        copied = _copy_state(state)
        _assert_faithful(copy.deepcopy(state), copied)

    @pytest.mark.parametrize(
        "value",
        [
            {},
            {"a": []},
            {"a": [[], [{}]]},
            {"a": {"b": {"c": None}}},
            {"unicode": "é✓", "empty_str": ""},
            {"zero": 0, "false": False, "float_zero": 0.0},
        ],
    )
    def test_edge_shapes_match_deepcopy(self, value: dict[str, Any]) -> None:
        _assert_faithful(copy.deepcopy(value), _copy_state(value))

    def test_mutating_the_source_does_not_touch_the_copy(self) -> None:
        state = {"trajectory": [{"args": {"q": "orig"}}], "plugin_data": {"n": [1]}}
        copied = _copy_state(state)
        state["trajectory"][0]["args"]["q"] = "mutated"  # type: ignore[index]
        state["plugin_data"]["n"].append(2)  # type: ignore[union-attr]
        assert copied["trajectory"][0]["args"]["q"] == "orig"
        assert copied["plugin_data"]["n"] == [1]

    def test_self_referential_state_falls_back_to_deepcopy(self) -> None:
        state: dict[str, Any] = {"a": [1, 2]}
        state["self"] = state
        state["a"].append(state["a"])
        copied = _copy_state(state)
        assert copied is not state
        assert copied["self"] is copied
        assert copied["a"][2] is copied["a"]


class TestStoreIsolation:
    async def test_save_snapshots_state_at_save_time(self) -> None:
        store = InMemoryCheckpointStore()
        checkpoint = Checkpoint(run_id="r1", query="q")
        checkpoint.plugin_data["notes"] = ["first"]
        await store.save(checkpoint)

        checkpoint.plugin_data["notes"].append("after-save")
        loaded = await store.load("r1")
        assert loaded is not None
        assert loaded.plugin_data["notes"] == ["first"]

    async def test_mutating_a_loaded_checkpoint_does_not_reach_the_store(self) -> None:
        store = InMemoryCheckpointStore()
        checkpoint = Checkpoint(run_id="r1")
        checkpoint.steps["0:tool:h"] = {"result": {"items": [1, 2]}}
        await store.save(checkpoint)

        first = await store.load("r1")
        assert first is not None
        first.steps["0:tool:h"]["result"]["items"].append(3)

        second = await store.load("r1")
        assert second is not None
        assert second.steps["0:tool:h"]["result"]["items"] == [1, 2]

    async def test_exotic_state_survives_a_store_round_trip(self) -> None:
        store = InMemoryCheckpointStore()
        checkpoint = Checkpoint(run_id="r1")
        checkpoint.plugin_data = _exotic_state()
        await store.save(checkpoint)

        loaded = await store.load("r1")
        assert loaded is not None
        _assert_faithful(_exotic_state(), loaded.plugin_data)

    async def test_history_snapshots_are_independent_of_later_saves(self) -> None:
        store = InMemoryCheckpointStore(history_enabled=True)
        checkpoint = Checkpoint(run_id="r1")
        checkpoint.plugin_data["stage"] = ["one"]
        await store.save(checkpoint)
        first_version = checkpoint.version

        checkpoint.plugin_data["stage"].append("two")
        await store.save(checkpoint)

        snapshot = await store.load_snapshot("r1", first_version)
        assert snapshot is not None
        assert snapshot.plugin_data["stage"] == ["one"]
