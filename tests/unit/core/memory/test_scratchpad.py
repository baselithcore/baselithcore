"""Unit tests for ``core.memory.scratchpad``."""

from __future__ import annotations

import pytest

from core.memory.scratchpad import (
    DEFAULT_MAX_SECTION_BYTES,
    InMemoryScratchpadBackend,
    Scratchpad,
    ScratchpadOverflowError,
)


def _pad() -> Scratchpad:
    return Scratchpad(backend=InMemoryScratchpadBackend())


class TestScratchpad:
    def test_update_and_read_roundtrip(self) -> None:
        p = _pad()
        p.update_section("t1", "goal", "find prime numbers")
        assert p.read_section("t1", "goal") == "find prime numbers"

    def test_overwrite_replaces_content(self) -> None:
        p = _pad()
        p.update_section("t1", "plan", "step 1")
        p.update_section("t1", "plan", "step 1; step 2")
        assert p.read_section("t1", "plan") == "step 1; step 2"

    def test_read_unknown_section_returns_none(self) -> None:
        p = _pad()
        assert p.read_section("t1", "missing") is None

    def test_threads_are_isolated(self) -> None:
        p = _pad()
        p.update_section("alice", "secret", "A")
        p.update_section("bob", "secret", "B")
        assert p.read_section("alice", "secret") == "A"
        assert p.read_section("bob", "secret") == "B"

    def test_clear_section(self) -> None:
        p = _pad()
        p.update_section("t1", "a", "x")
        p.update_section("t1", "b", "y")
        p.clear_section("t1", "a")
        assert p.read_section("t1", "a") is None
        assert p.read_section("t1", "b") == "y"

    def test_clear_all(self) -> None:
        p = _pad()
        p.update_section("t1", "a", "x")
        p.update_section("t1", "b", "y")
        p.clear("t1")
        assert p.list_sections("t1") == []

    def test_list_sections_sorted(self) -> None:
        p = _pad()
        p.update_section("t1", "zeta", "z")
        p.update_section("t1", "alpha", "a")
        assert p.list_sections("t1") == ["alpha", "zeta"]

    def test_read_all_concatenates_markdown(self) -> None:
        p = _pad()
        p.update_section("t1", "goal", "ship feature")
        p.update_section("t1", "plan", "1. design\n2. build")
        text = p.read_all("t1")
        assert "## goal" in text
        assert "ship feature" in text
        assert "## plan" in text
        assert "1. design" in text

    def test_read_all_empty(self) -> None:
        p = _pad()
        assert p.read_all("nobody") == ""

    def test_section_byte_cap_enforced(self) -> None:
        p = Scratchpad(
            backend=InMemoryScratchpadBackend(),
            max_section_bytes=16,
        )
        with pytest.raises(ScratchpadOverflowError):
            p.update_section("t1", "big", "x" * 17)

    def test_section_count_cap_enforced(self) -> None:
        p = Scratchpad(
            backend=InMemoryScratchpadBackend(),
            max_sections=2,
        )
        p.update_section("t1", "a", "1")
        p.update_section("t1", "b", "2")
        with pytest.raises(ScratchpadOverflowError):
            p.update_section("t1", "c", "3")

    def test_section_count_cap_allows_overwrite_at_limit(self) -> None:
        p = Scratchpad(
            backend=InMemoryScratchpadBackend(),
            max_sections=2,
        )
        p.update_section("t1", "a", "1")
        p.update_section("t1", "b", "2")
        p.update_section("t1", "a", "1-updated")
        assert p.read_section("t1", "a") == "1-updated"

    def test_empty_section_name_rejected(self) -> None:
        p = _pad()
        with pytest.raises(ValueError):
            p.update_section("t1", "", "x")

    def test_default_cap_is_8kb(self) -> None:
        assert DEFAULT_MAX_SECTION_BYTES == 8 * 1024


class TestInMemoryBackend:
    def test_delete_unknown_section_is_noop(self) -> None:
        b = InMemoryScratchpadBackend()
        b.delete("nobody", "missing")

    def test_set_then_get(self) -> None:
        b = InMemoryScratchpadBackend()
        b.set("t1", "k", "v")
        assert b.get("t1", "k") == "v"
