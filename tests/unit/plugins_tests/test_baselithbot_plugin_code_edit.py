"""Unit tests for the Baselithbot plugin — code editing layer."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_code_search_replace_literal(tmp_path) -> None:
    from plugins.baselithbot.code_edit import (
        SearchReplaceEdit,
        apply_search_replace,
    )
    from plugins.baselithbot.computer_use.config import AuditLogger, ComputerUseConfig
    from plugins.baselithbot.computer_use.filesystem import ScopedFileSystem

    root = tmp_path / "ws"
    root.mkdir()
    (root / "f.txt").write_text("hello world")
    cfg = ComputerUseConfig(
        enabled=True, allow_filesystem=True, filesystem_root=str(root)
    )
    fs = ScopedFileSystem(cfg, AuditLogger(None))
    out = await apply_search_replace(
        SearchReplaceEdit(path="f.txt", pattern="world", replacement="baselithbot"),
        fs,
    )
    assert out["matches"] == 1
    assert (root / "f.txt").read_text() == "hello baselithbot"


@pytest.mark.asyncio
async def test_code_multi_file_write_atomic_rollback(tmp_path) -> None:
    from plugins.baselithbot.code_edit import MultiFileEdit, MultiFileEditor
    from plugins.baselithbot.computer_use.config import AuditLogger, ComputerUseConfig
    from plugins.baselithbot.computer_use.filesystem import ScopedFileSystem

    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("AA")
    (root / "b.txt").write_text("BB")

    cfg = ComputerUseConfig(
        enabled=True, allow_filesystem=True, filesystem_root=str(root)
    )
    fs = ScopedFileSystem(cfg, AuditLogger(None))
    editor = MultiFileEditor(fs)

    out = await editor.apply(
        [
            MultiFileEdit(path="a.txt", content="A2"),
            MultiFileEdit(path="b.txt", content="B2"),
        ]
    )
    assert out["status"] == "success"
    assert (root / "a.txt").read_text() == "A2"
    assert (root / "b.txt").read_text() == "B2"


@pytest.mark.asyncio
async def test_code_unified_diff_apply(tmp_path) -> None:
    from plugins.baselithbot.code_edit import apply_unified_diff
    from plugins.baselithbot.computer_use.config import AuditLogger, ComputerUseConfig
    from plugins.baselithbot.computer_use.filesystem import ScopedFileSystem

    root = tmp_path / "ws"
    root.mkdir()
    (root / "demo.txt").write_text("alpha\nbeta\ngamma\n")
    cfg = ComputerUseConfig(
        enabled=True, allow_filesystem=True, filesystem_root=str(root)
    )
    fs = ScopedFileSystem(cfg, AuditLogger(None))

    diff = (
        "--- a/demo.txt\n+++ b/demo.txt\n"
        "@@ -1,3 +1,3 @@\n alpha\n-beta\n+BETA\n gamma\n"
    )
    out = await apply_unified_diff(diff, fs)
    assert out["status"] == "success"
    assert (root / "demo.txt").read_text() == "alpha\nBETA\ngamma\n"


@pytest.mark.asyncio
async def test_rename_symbol_or_skip_if_libcst_missing(tmp_path) -> None:
    from plugins.baselithbot.code_edit import ASTRefactorError, rename_symbol
    from plugins.baselithbot.computer_use.config import AuditLogger, ComputerUseConfig
    from plugins.baselithbot.computer_use.filesystem import ScopedFileSystem

    root = tmp_path / "ws"
    root.mkdir()
    (root / "f.py").write_text("def foo():\n    foo_value = 1\n    return foo_value\n")
    cfg = ComputerUseConfig(
        enabled=True, allow_filesystem=True, filesystem_root=str(root)
    )
    fs = ScopedFileSystem(cfg, AuditLogger(None))

    try:
        out = await rename_symbol("f.py", "foo", "bar", fs)
    except ASTRefactorError as exc:
        pytest.skip(f"libcst unavailable: {exc}")

    assert out["status"] == "success"
    text = (root / "f.py").read_text()
    assert "def bar()" in text
    assert "foo_value" in text
