"""Unit tests for document-aware splitters (markdown, python, selector)."""

from pathlib import Path

import pytest
from core.services.vectorstore.splitters import (
    MarkdownHeaderSplitter,
    PythonCodeSplitter,
    RecursiveTextSplitter,
    SplitterProtocol,
    TextChunk,
    select_splitter,
)

MARKDOWN_DOC = """intro text before any heading

# Title

Intro paragraph under title.

## Section

Body of section.

### Sub

Deep body.

## Other

Other body.
"""


class TestMarkdownHeaderSplitter:
    def test_heading_paths(self):
        splitter = MarkdownHeaderSplitter()
        chunks = splitter.split(MARKDOWN_DOC)

        assert all(isinstance(c, TextChunk) for c in chunks)
        paths = [c.metadata["headings"] for c in chunks]
        assert paths == [
            [],
            ["Title"],
            ["Title", "Section"],
            ["Title", "Section", "Sub"],
            ["Title", "Other"],
        ]

    def test_chunk_texts_carry_heading_and_body(self):
        splitter = MarkdownHeaderSplitter()
        chunks = splitter.split(MARKDOWN_DOC)

        assert chunks[0].text == "intro text before any heading"
        assert chunks[1].text.startswith("# Title")
        assert "Intro paragraph under title." in chunks[1].text
        assert chunks[2].text.startswith("## Section")
        assert "Body of section." in chunks[2].text

    def test_sibling_heading_resets_deeper_levels(self):
        splitter = MarkdownHeaderSplitter()
        chunks = splitter.split(MARKDOWN_DOC)
        # "Other" is a level-2 sibling of "Section": the level-3 "Sub"
        # entry must not leak into its path.
        assert chunks[-1].metadata["headings"] == ["Title", "Other"]

    def test_heading_inside_code_fence_is_not_a_boundary(self):
        text = "# Real\n\nbody\n\n```\n# not a heading\ncode line\n```\nafter fence\n"
        chunks = MarkdownHeaderSplitter().split(text)

        assert len(chunks) == 1
        assert chunks[0].metadata["headings"] == ["Real"]
        assert "# not a heading" in chunks[0].text

    def test_oversized_section_falls_back_to_recursive(self):
        body = "word " * 60  # ~300 chars, far over the 80-char cap
        text = f"# Big\n\n{body.strip()}\n"
        splitter = MarkdownHeaderSplitter(chunk_size=80, chunk_overlap=0)
        chunks = splitter.split(text)

        assert len(chunks) > 1
        assert all(len(c.text) <= 80 for c in chunks)
        assert all(c.metadata["headings"] == ["Big"] for c in chunks)

    def test_split_text_conforms_to_interface(self):
        splitter = MarkdownHeaderSplitter()
        texts = splitter.split_text(MARKDOWN_DOC)
        assert texts == [c.text for c in splitter.split(MARKDOWN_DOC)]
        assert all(isinstance(t, str) for t in texts)

    def test_empty_input(self):
        assert MarkdownHeaderSplitter().split("") == []
        assert MarkdownHeaderSplitter().split("   \n \n") == []


PYTHON_SOURCE = '''"""Module docstring."""

import os

CONST = 1


def plain(x):
    """Doc."""
    return x + CONST


@decorator
@another(arg=2)
def decorated(y):
    """Decorated doc."""
    return y


class Thing:
    """Class doc."""

    def method(self):
        return os.name


if __name__ == "__main__":
    plain(1)
'''


class TestPythonCodeSplitter:
    def test_top_level_units(self):
        chunks = PythonCodeSplitter().split(PYTHON_SOURCE)
        kinds_names = [(c.metadata["kind"], c.metadata.get("name")) for c in chunks]

        assert kinds_names == [
            ("module", None),
            ("function", "plain"),
            ("function", "decorated"),
            ("class", "Thing"),
            ("module", None),
        ]

    def test_preamble_is_own_chunk(self):
        chunks = PythonCodeSplitter().split(PYTHON_SOURCE)
        preamble = chunks[0]
        assert preamble.metadata["kind"] == "module"
        assert '"""Module docstring."""' in preamble.text
        assert "import os" in preamble.text
        assert "CONST = 1" in preamble.text
        assert "def plain" not in preamble.text

    def test_decorated_function_includes_decorators_and_docstring(self):
        chunks = PythonCodeSplitter().split(PYTHON_SOURCE)
        decorated = next(c for c in chunks if c.metadata.get("name") == "decorated")
        assert decorated.text.startswith("@decorator")
        assert "@another(arg=2)" in decorated.text
        assert '"""Decorated doc."""' in decorated.text

    def test_class_unit_includes_methods(self):
        chunks = PythonCodeSplitter().split(PYTHON_SOURCE)
        thing = next(c for c in chunks if c.metadata.get("name") == "Thing")
        assert thing.metadata["kind"] == "class"
        assert "def method" in thing.text

    def test_trailing_module_code_is_module_chunk(self):
        chunks = PythonCodeSplitter().split(PYTHON_SOURCE)
        assert chunks[-1].metadata["kind"] == "module"
        assert "__main__" in chunks[-1].text

    def test_syntax_error_falls_back_to_recursive(self):
        broken = "def broken(:\n    pass\n" + ("x = 1\n" * 50)
        chunks = PythonCodeSplitter(chunk_size=100, chunk_overlap=0).split(broken)

        assert chunks
        assert all(c.metadata["kind"] == "fallback" for c in chunks)
        assert all(len(c.text) <= 100 for c in chunks)

    def test_split_text_conforms_to_interface(self):
        splitter = PythonCodeSplitter()
        texts = splitter.split_text(PYTHON_SOURCE)
        assert texts == [c.text for c in splitter.split(PYTHON_SOURCE)]

    def test_empty_input(self):
        assert PythonCodeSplitter().split("") == []


class TestSelectSplitter:
    @pytest.mark.parametrize("source", ["notes.md", "dir/notes.markdown"])
    def test_markdown_by_extension(self, source):
        assert isinstance(select_splitter(source), MarkdownHeaderSplitter)

    def test_python_by_extension(self):
        assert isinstance(select_splitter("mod.py"), PythonCodeSplitter)

    def test_path_object(self):
        assert isinstance(select_splitter(Path("/a/b/c.py")), PythonCodeSplitter)

    def test_mime_markdown(self):
        assert isinstance(
            select_splitter(None, mime="text/markdown"), MarkdownHeaderSplitter
        )

    def test_mime_python(self):
        assert isinstance(
            select_splitter(None, mime="text/x-python"), PythonCodeSplitter
        )

    def test_default_recursive(self):
        assert isinstance(select_splitter("data.txt"), RecursiveTextSplitter)
        assert isinstance(select_splitter(None), RecursiveTextSplitter)

    def test_all_conform_to_splitter_protocol(self):
        for source in ("a.md", "a.py", "a.txt", None):
            splitter = select_splitter(source)
            assert isinstance(splitter, SplitterProtocol)
            chunks = splitter.split_text("hello world")
            assert chunks == ["hello world"]


class TestRecursiveTextSplitter:
    def test_splits_long_text(self):
        splitter = RecursiveTextSplitter(chunk_size=50, chunk_overlap=0)
        text = "one two three. " * 20
        parts = splitter.split_text(text)
        assert len(parts) > 1
        assert all(len(p) <= 50 for p in parts)

    def test_split_returns_chunks_with_empty_metadata(self):
        chunks = RecursiveTextSplitter().split("hello")
        assert chunks == [TextChunk(text="hello", metadata={})]
