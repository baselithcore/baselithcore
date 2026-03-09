import pytest
import html
from pathlib import Path
from core.services.sanitization import (
    InputSanitizer,
    sanitize_query,
    sanitize_html,
    sanitize_identifier,
)


class TestInputSanitizer:
    def test_sanitize_query_basic(self):
        assert InputSanitizer.sanitize_query("  test  ") == "test"
        assert InputSanitizer.sanitize_query("") == ""

    def test_sanitize_query_dangerous_patterns(self):
        # Script tags
        assert (
            InputSanitizer.sanitize_query("<script>alert(1)</script>hello") == "hello"
        )
        # JavaScript protocol
        assert InputSanitizer.sanitize_query("javascript:alert(1)") == "alert(1)"
        # Event handlers
        assert InputSanitizer.sanitize_query("onclick=alert(1)") == "alert(1)"
        # Null bytes
        assert InputSanitizer.sanitize_query("test\x00data") == "testdata"

    def test_sanitize_query_max_length(self):
        long_query = "a" * 11000
        sanitized = InputSanitizer.sanitize_query(long_query)
        assert len(sanitized) == 10000
        assert sanitized == "a" * 10000

        # Test custom max length
        assert len(InputSanitizer.sanitize_query("abcde", max_length=3)) == 3

    def test_sanitize_html(self):
        raw = "<p>Hello & Welcome</p>"
        # Expected: &lt;p&gt;Hello &amp; Welcome&lt;/p&gt;
        # But wait, sanitize_html with allow_markdown=True (default) restores &amp;
        expected = html.escape(raw).replace("&amp;", "&")
        assert InputSanitizer.sanitize_html(raw) == expected

        # Without markdown preservation
        assert InputSanitizer.sanitize_html(raw, allow_markdown=False) == html.escape(
            raw
        )

    def test_sanitize_path_safety(self, tmp_path):
        # Basic resolution
        path_str = str(tmp_path / "test.txt")
        assert InputSanitizer.sanitize_path(path_str) == str(Path(path_str).resolve())

        # Traversal protection
        base = tmp_path / "base"
        base.mkdir()
        safe_file = base / "safe.txt"
        safe_file.touch()

        # Valid path within base
        assert InputSanitizer.sanitize_path(str(safe_file), base_path=base) == str(
            safe_file.resolve()
        )

        # Traversal attempt
        unsafe_path = str(base / "../outside.txt")
        with pytest.raises(ValueError, match="Path traversal detected"):
            InputSanitizer.sanitize_path(unsafe_path, base_path=base)

    def test_sanitize_path_protocol_strip(self):
        assert InputSanitizer.sanitize_path("file:///tmp/test") == str(
            Path("/tmp/test").resolve()
        )

    def test_sanitize_identifier(self):
        assert InputSanitizer.sanitize_identifier("valid_id-123") == "valid_id-123"
        assert InputSanitizer.sanitize_identifier("invalid@id#") == "invalidid"
        assert InputSanitizer.sanitize_identifier("a" * 300) == "a" * 256
        assert InputSanitizer.sanitize_identifier("") == ""

    def test_sanitize_dict(self):
        data = {
            "id": "session-123",
            "query": "<script>evil</script>hello",
            "meta": {"tags": ["safe", "javascript:void(0)"], "depth": 1},
            "": "empty key",
        }
        sanitized = InputSanitizer.sanitize_dict(data)

        assert sanitized["id"] == "session-123"
        assert sanitized["query"] == "hello"
        assert sanitized["meta"]["tags"] == ["safe", "void(0)"]
        assert sanitized["meta"]["depth"] == 1
        assert "" in sanitized

    def test_convenience_functions(self):
        assert sanitize_query(" test ") == "test"
        assert sanitize_html("<b>") == "&lt;b&gt;"
        assert sanitize_identifier("a!") == "a"
        # sanitize_path is already tested via InputSanitizer
