"""Unit tests for ``core.plugins.result``."""

from __future__ import annotations

import pytest

from core.plugins.result import (
    SNAPSHOT_MAX_CHARS,
    SkillResult,
    fail,
    ok,
    partial,
)


class TestSkillResultFactories:
    def test_ok_default(self) -> None:
        r = ok()
        assert r.success is True
        assert r.data is None
        assert r.snapshot is None
        assert r.error_code is None
        assert r.metadata == {}

    def test_ok_with_string_data_uses_data_as_snapshot(self) -> None:
        r = ok(data="hello world", message="done")
        assert r.success is True
        assert r.snapshot == "hello world"
        assert r.message == "done"

    def test_ok_with_dict_data_snapshots_as_json(self) -> None:
        r = ok(data={"key": "value", "n": 1})
        assert r.snapshot is not None
        assert '"key"' in r.snapshot
        assert '"value"' in r.snapshot

    def test_ok_with_explicit_snapshot_wins(self) -> None:
        r = ok(data="raw", snapshot="custom preview")
        assert r.snapshot == "custom preview"

    def test_ok_long_data_is_truncated(self) -> None:
        long = "x" * (SNAPSHOT_MAX_CHARS + 100)
        r = ok(data=long)
        assert r.snapshot is not None
        assert len(r.snapshot) == SNAPSHOT_MAX_CHARS + 1
        assert r.snapshot.endswith("…")

    def test_ok_with_metadata(self) -> None:
        r = ok(data=1, metadata={"latency_ms": 42})
        assert r.metadata == {"latency_ms": 42}

    def test_fail_carries_error_code(self) -> None:
        r = fail("nope", error_code="not_found")
        assert r.success is False
        assert r.error_code == "not_found"
        assert r.message == "nope"

    def test_fail_default_error_code(self) -> None:
        r = fail("boom")
        assert r.error_code == "skill_error"

    def test_partial_marks_metadata(self) -> None:
        r = partial(data={"got": 1}, message="incomplete")
        assert r.success is False
        assert r.metadata.get("partial") is True
        assert r.error_code == "skill_partial"
        assert r.data == {"got": 1}

    def test_unserializable_data_falls_back_to_repr(self) -> None:
        class NotJSON:
            def __repr__(self) -> str:
                return "<NotJSON>"

        r = ok(data=NotJSON())
        assert r.snapshot is not None
        # default=str handles most cases; repr fallback for hostile objects
        assert "NotJSON" in r.snapshot

    def test_result_is_frozen(self) -> None:
        r = ok(data="x")
        with pytest.raises(Exception):
            r.success = False  # type: ignore[misc]


class TestSkillResultIntegration:
    def test_envelope_exposed_from_plugins_package(self) -> None:
        from core.plugins import SkillResult as ExportedResult
        from core.plugins import fail as exported_fail
        from core.plugins import ok as exported_ok
        from core.plugins import partial as exported_partial

        assert ExportedResult is SkillResult
        assert exported_ok is ok
        assert exported_fail is fail
        assert exported_partial is partial
