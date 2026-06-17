"""Tests for cursor-based pagination."""

import pytest

from core.api.pagination import (
    CursorPage,
    PaginationError,
    decode_cursor,
    encode_cursor,
    normalize_limit,
    paginate_sequence,
)


class TestCursorCodec:
    def test_roundtrip(self):
        token = encode_cursor({"offset": 42, "k": "v"})
        assert decode_cursor(token) == {"offset": 42, "k": "v"}

    def test_opaque_no_padding(self):
        token = encode_cursor({"offset": 1})
        assert "=" not in token  # base64url padding stripped

    def test_malformed_raises(self):
        with pytest.raises(PaginationError):
            decode_cursor("!!!not-base64!!!")

    def test_non_dict_payload_rejected(self):
        import base64
        import orjson

        bad = base64.urlsafe_b64encode(orjson.dumps([1, 2, 3])).decode().rstrip("=")
        with pytest.raises(PaginationError):
            decode_cursor(bad)


class TestNormalizeLimit:
    def test_default(self):
        assert normalize_limit(None) == 50

    def test_clamp_to_max(self):
        assert normalize_limit(9999, max_limit=200) == 200

    def test_rejects_zero(self):
        with pytest.raises(PaginationError):
            normalize_limit(0)


class TestPaginateSequence:
    def test_first_page_has_more(self):
        page = paginate_sequence(list(range(10)), limit=3)
        assert isinstance(page, CursorPage)
        assert page.items == [0, 1, 2]
        assert page.has_more is True
        assert page.next_cursor is not None
        assert page.limit == 3

    def test_walks_all_pages(self):
        data = list(range(7))
        seen = []
        cursor = None
        for _ in range(10):  # safety bound
            page = paginate_sequence(data, limit=3, cursor=cursor)
            seen.extend(page.items)
            if not page.has_more:
                break
            cursor = page.next_cursor
        assert seen == data

    def test_last_page_no_cursor(self):
        page = paginate_sequence([1, 2], limit=5)
        assert page.items == [1, 2]
        assert page.has_more is False
        assert page.next_cursor is None

    def test_empty_sequence(self):
        page = paginate_sequence([], limit=5)
        assert page.items == []
        assert page.has_more is False
        assert page.next_cursor is None

    def test_limit_clamped(self):
        page = paginate_sequence(list(range(500)), limit=9999, max_limit=100)
        assert len(page.items) == 100
        assert page.limit == 100

    def test_bad_cursor_offset_rejected(self):
        bad = encode_cursor({"offset": -5})
        with pytest.raises(PaginationError):
            paginate_sequence([1, 2, 3], cursor=bad)
