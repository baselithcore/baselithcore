"""
Tests for vectorstore chunking module.
"""

from core.services.vectorstore.chunking import (
    chunk_text,
    prepare_chunk_text,
    chunk_point_id,
    DEFAULT_SPLITTER,
)


class TestChunkText:
    """Tests for chunk_text function."""

    def test_empty_text_returns_empty_list(self):
        """Chunking empty text returns empty list."""
        result = chunk_text("")
        assert result == []

    def test_whitespace_only_returns_empty(self):
        """Whitespace-only text returns empty list."""
        result = chunk_text("   \n\t  ")
        assert result == []

    def test_short_text_single_chunk(self):
        """Short text returns single chunk."""
        text = "This is a short text."
        result = chunk_text(text, chunk_size=1000)
        assert len(result) == 1
        assert text in result[0]

    def test_long_text_multiple_chunks(self):
        """Long text is split into multiple chunks."""
        text = "A" * 2000
        result = chunk_text(text, chunk_size=500, chunk_overlap=50)
        assert len(result) > 1

    def test_default_parameters(self):
        """Default parameters work correctly."""
        text = "Sample text " * 100
        result = chunk_text(text)
        assert isinstance(result, list)

    def test_custom_chunk_size(self):
        """Custom chunk size is respected."""
        text = "Word " * 200  # ~1000 chars
        result = chunk_text(text, chunk_size=200, chunk_overlap=20)
        assert len(result) > 1
        for chunk in result:
            # Each chunk should be around chunk_size (with some flexibility)
            assert len(chunk) <= 300  # Some buffer for splitter behavior


class TestPrepareChunkText:
    """Tests for prepare_chunk_text function."""

    def test_no_metadata_returns_original(self):
        """Without metadata, returns original chunk."""
        chunk = "Original text"
        result = prepare_chunk_text(chunk)
        assert result == chunk

    def test_none_metadata_returns_original(self):
        """None metadata returns original chunk."""
        chunk = "Original text"
        result = prepare_chunk_text(chunk, None)
        assert result == chunk

    def test_empty_metadata_returns_original(self):
        """Empty metadata dict returns original chunk."""
        chunk = "Original text"
        result = prepare_chunk_text(chunk, {})
        assert result == chunk

    def test_filename_metadata_prepended(self):
        """Filename is prepended to chunk."""
        chunk = "Content here"
        metadata = {"filename": "document.pdf"}
        result = prepare_chunk_text(chunk, metadata)
        assert "File: document.pdf" in result
        assert "Content here" in result

    def test_source_metadata_prepended(self):
        """Source is prepended when no filename."""
        chunk = "Content here"
        metadata = {"source": "web_page"}
        result = prepare_chunk_text(chunk, metadata)
        assert "Source: web_page" in result

    def test_filename_preferred_over_source(self):
        """Filename is used when both are present."""
        chunk = "Content"
        metadata = {"filename": "doc.txt", "source": "upload"}
        result = prepare_chunk_text(chunk, metadata)
        assert "File: doc.txt" in result
        assert "Source: upload" not in result


class TestChunkPointId:
    """Tests for chunk_point_id function."""

    def test_returns_integer(self):
        """Returns an integer ID."""
        result = chunk_point_id("doc123", 0)
        assert isinstance(result, int)

    def test_deterministic(self):
        """Same inputs always produce same ID."""
        id1 = chunk_point_id("document", 5)
        id2 = chunk_point_id("document", 5)
        assert id1 == id2

    def test_different_docs_different_ids(self):
        """Different documents have different IDs."""
        id1 = chunk_point_id("doc1", 0)
        id2 = chunk_point_id("doc2", 0)
        assert id1 != id2

    def test_different_chunks_different_ids(self):
        """Different chunk indices have different IDs."""
        id1 = chunk_point_id("doc", 0)
        id2 = chunk_point_id("doc", 1)
        assert id1 != id2


class TestDefaultSplitter:
    """Tests for DEFAULT_SPLITTER constant."""

    def test_default_splitter_exists(self):
        """DEFAULT_SPLITTER is defined."""
        assert DEFAULT_SPLITTER is not None

    def test_default_splitter_has_split_text(self):
        """DEFAULT_SPLITTER has split_text method."""
        assert hasattr(DEFAULT_SPLITTER, "split_text")
        assert callable(DEFAULT_SPLITTER.split_text)
