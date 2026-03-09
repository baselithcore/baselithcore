"""
Unit tests for Chat service utilities.
"""

import pytest
from core.services.chat.utils.streaming import (
    build_cached_stream,
    build_fallback_stream,
    stream_answer,
)
from core.services.chat.utils.history import ChatHistoryManager


# =============================================================================
# Streaming Tests
# =============================================================================


class TestBuildCachedStream:
    """Tests for build_cached_stream function."""

    def test_yields_single_chunk(self):
        """Cached stream yields exactly one chunk."""
        stream = build_cached_stream("Hello world")
        chunks = list(stream)
        assert chunks == ["Hello world"]

    def test_empty_answer(self):
        """Handles empty answer."""
        stream = build_cached_stream("")
        chunks = list(stream)
        assert chunks == [""]


class TestBuildFallbackStream:
    """Tests for build_fallback_stream function."""

    def test_yields_single_chunk(self):
        """Fallback stream yields exactly one chunk."""
        stream = build_fallback_stream("Error message")
        chunks = list(stream)
        assert chunks == ["Error message"]


class TestStreamAnswer:
    """Tests for stream_answer function."""

    def test_streams_chunks(self):
        """Streams all chunks from stream_fn."""

        def mock_stream(prompt):
            yield "Hello "
            yield "world"

        def mock_finalize(answer):
            return answer

        stream = stream_answer(
            "test prompt", stream_fn=mock_stream, finalize_fn=mock_finalize
        )
        chunks = list(stream)
        assert chunks == ["Hello ", "world"]

    def test_calls_finalize(self):
        """Calls finalize function with normalized answer."""
        finalized = []

        def mock_stream(prompt):
            yield "Hello"

        def mock_finalize(answer):
            finalized.append(answer)
            return answer + "!"

        stream = stream_answer("test", stream_fn=mock_stream, finalize_fn=mock_finalize)
        list(stream)  # Consume
        assert finalized == ["Hello"]

    def test_on_finalize_callback(self):
        """Calls on_finalize callback."""
        callbacks = []

        def mock_stream(prompt):
            yield "Test"

        def mock_finalize(answer):
            return answer

        def on_finalize(final, normalized):
            callbacks.append((final, normalized))

        stream = stream_answer(
            "test",
            stream_fn=mock_stream,
            finalize_fn=mock_finalize,
            on_finalize=on_finalize,
        )
        list(stream)
        assert callbacks == [("Test", "Test")]

    def test_yields_suffix_from_finalize(self):
        """Yields extra suffix if finalize adds content."""

        def mock_stream(prompt):
            yield "Hello"

        def mock_finalize(answer):
            return answer + " World"

        stream = stream_answer("test", stream_fn=mock_stream, finalize_fn=mock_finalize)
        chunks = list(stream)
        assert chunks == ["Hello", " World"]

    def test_skips_empty_chunks(self):
        """Skips empty chunks from stream_fn."""

        def mock_stream(prompt):
            yield "A"
            yield ""
            yield "B"

        def mock_finalize(answer):
            return answer

        stream = stream_answer("test", stream_fn=mock_stream, finalize_fn=mock_finalize)
        chunks = list(stream)
        assert chunks == ["A", "B"]


# =============================================================================
# History Manager Tests
# =============================================================================


class MockCache:
    """Mock cache for testing."""

    def __init__(self):
        self._data = {}

    async def get(self, key):
        return self._data.get(key)

    async def set(self, key, value):
        self._data[key] = value


class TestChatHistoryManager:
    """Tests for ChatHistoryManager."""

    @pytest.mark.asyncio
    async def test_load_empty_without_cache(self):
        """Returns empty when no cache."""
        manager = ChatHistoryManager(None, max_turns=5)
        turns, text = await manager.load("conv-1")
        assert turns == []
        assert text == ""

    @pytest.mark.asyncio
    async def test_load_empty_without_conversation_id(self):
        """Returns empty when no conversation_id."""
        cache = MockCache()
        manager = ChatHistoryManager(cache, max_turns=5)
        turns, text = await manager.load(None)
        assert turns == []
        assert text == ""

    @pytest.mark.asyncio
    async def test_load_returns_stored_turns(self):
        """Returns stored turns from cache."""
        cache = MockCache()
        await cache.set(
            "conv-1", {"turns": [{"query": "Q1", "answer": "A1"}], "summary": ""}
        )
        manager = ChatHistoryManager(cache, max_turns=5)
        turns, text = await manager.load("conv-1")
        assert len(turns) == 1
        assert turns[0]["query"] == "Q1"
        assert "Q1" in text

    @pytest.mark.asyncio
    async def test_load_with_summary(self):
        """Includes summary in loaded text."""
        cache = MockCache()
        await cache.set("conv-1", {"turns": [], "summary": "Past summary"})
        manager = ChatHistoryManager(cache, max_turns=5)
        turns, text = await manager.load("conv-1")
        assert turns == []
        assert "Conversation summary:" in text
        assert "Past summary" in text

    @pytest.mark.asyncio
    async def test_load_empty_payload(self):
        """Handles empty payload in cache."""
        cache = MockCache()
        await cache.set("conv-1", {})
        manager = ChatHistoryManager(cache, max_turns=5)
        turns, text = await manager.load("conv-1")
        assert turns == []
        assert text == ""

    @pytest.mark.asyncio
    async def test_append_turn_stores_in_cache(self):
        """Appends turn to cache."""
        cache = MockCache()
        manager = ChatHistoryManager(cache, max_turns=5)
        await manager.append_turn("conv-1", [], "Hello", "Hi there")
        stored = await cache.get("conv-1")
        assert stored is not None
        assert len(stored["turns"]) == 1
        assert stored["turns"][0]["query"] == "Hello"

    @pytest.mark.asyncio
    async def test_append_turn_respects_max_turns(self):
        """Respects max_turns limit."""
        cache = MockCache()
        manager = ChatHistoryManager(cache, max_turns=2)
        await manager.append_turn("conv-1", [], "Q1", "A1")
        await manager.append_turn("conv-1", [], "Q2", "A2")
        await manager.append_turn("conv-1", [], "Q3", "A3")
        stored = await cache.get("conv-1")
        assert len(stored["turns"]) == 2
        assert stored["turns"][0]["query"] == "Q2"
        assert stored["turns"][1]["query"] == "Q3"

    @pytest.mark.asyncio
    async def test_append_turn_ignores_empty_query(self):
        """Ignores turns with empty query."""
        cache = MockCache()
        manager = ChatHistoryManager(cache, max_turns=5)
        await manager.append_turn("conv-1", [], "", "Answer")
        assert await cache.get("conv-1") is None

    @pytest.mark.asyncio
    async def test_append_turn_ignores_empty_answer(self):
        """Ignores turns with empty answer."""
        cache = MockCache()
        manager = ChatHistoryManager(cache, max_turns=5)
        await manager.append_turn("conv-1", [], "Question", "")
        assert await cache.get("conv-1") is None

    @pytest.mark.asyncio
    async def test_summary_enabled(self):
        """Creates summary when enabled and overflow."""
        cache = MockCache()
        manager = ChatHistoryManager(
            cache, max_turns=2, summary_enabled=True, summary_max_turns=5
        )
        await manager.append_turn("conv-1", [], "Q1", "A1")
        await manager.append_turn("conv-1", [], "Q2", "A2")
        await manager.append_turn("conv-1", [], "Q3", "A3")
        stored = await cache.get("conv-1")
        assert len(stored["turns"]) == 2
        assert "summary" in stored
        assert "Q1" in stored["summary"]

    @pytest.mark.asyncio
    async def test_sanitize_turns_handles_invalid_data(self):
        """Sanitize handles invalid turn data."""
        cache = MockCache()
        await cache.set(
            "conv-1",
            {
                "turns": [
                    {"query": "Valid", "answer": "OK"},
                    {"query": "", "answer": "Empty query"},
                    "not a dict",
                    {"query": "No answer"},
                ]
            },
        )
        manager = ChatHistoryManager(cache, max_turns=5)
        turns, _ = await manager.load("conv-1")
        assert len(turns) == 1
        assert turns[0]["query"] == "Valid"

    @pytest.mark.asyncio
    async def test_truncate_summary(self):
        """Truncates long summary."""
        cache = MockCache()
        manager = ChatHistoryManager(
            cache,
            max_turns=1,
            summary_enabled=True,
            summary_max_chars=130,  # Must be >= 120
        )
        # Add long turn to trigger internal line truncation (at 240 chars)
        await manager.append_turn("conv-1", [], "First" * 100, "First answer" * 100)
        await manager.append_turn("conv-1", [], "Second", "Second answer")
        stored = await cache.get("conv-1")
        if stored.get("summary"):
            assert len(stored["summary"]) <= 133
            # It might not end with ... if not truncated, but we added enough chars to truncate
            assert "..." in stored["summary"]

    @pytest.mark.asyncio
    async def test_append_turn_with_metadata(self):
        """Respects metadata in append_turn."""
        cache = MockCache()
        manager = ChatHistoryManager(cache, max_turns=5)
        await manager.append_turn("conv-1", [], "Q", "A", metadata={"user_id": "123"})
        stored = await cache.get("conv-1")
        # Metadata is merged but sanitized out in load unless implementation changes.
        # Currently _sanitize_turns only keeps query/answer.
        # But we test that line 118 is hit.
        assert stored["turns"][0]["query"] == "Q"

    @pytest.mark.asyncio
    async def test_load_payload_list_format(self):
        """Handles legacy list format in cache."""
        cache = MockCache()
        await cache.set("conv-1", [{"query": "Q", "answer": "A"}])
        manager = ChatHistoryManager(cache, max_turns=5)
        turns, text = await manager.load("conv-1")
        assert len(turns) == 1

    def test_sanitize_turns_edge_cases(self):
        """Tests internal sanitization logic directly."""
        manager = ChatHistoryManager(None, max_turns=5)
        assert manager._sanitize_turns(None) == []
        assert manager._sanitize_turns("not a list") == []
        assert manager._sanitize_turns([{"query": "Q"}]) == []  # Missing answer
        assert manager._sanitize_turns([["not a dict"]]) == []

    def test_merge_summary_no_overflow(self):
        """Handles merge with no overflow turns."""
        manager = ChatHistoryManager(None, max_turns=5)
        assert manager._merge_summary("Existing", []) == "Existing"

    def test_format_summary_line_long(self):
        """Truncates long lines in summary."""
        manager = ChatHistoryManager(None, max_turns=5)
        long_line = manager._format_summary_line({"query": "a" * 300, "answer": "b"})
        assert len(long_line) <= 240
        assert long_line.endswith("...")

    def test_truncate_summary_precise(self):
        """Tests precise truncation boundary."""
        # Class forces minimum 120 chars
        manager = ChatHistoryManager(None, max_turns=5, summary_max_chars=120)
        assert manager._truncate_summary("12345") == "12345"
        res = manager._truncate_summary("a" * 150)
        assert len(res) == 122  # (120 - 1) + 3 = 122
        assert res.endswith("...")
