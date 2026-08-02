"""Tests for pre-execution static analysis of sandboxed code."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Load the module by file path: importing the core.services.sandbox package
# pulls in the docker SDK, which is unavailable/broken in some test envs, and
# static analysis itself is dependency-free (stdlib ast only).
_MODULE_PATH = (
    Path(__file__).resolve().parents[5]
    / "core"
    / "services"
    / "sandbox"
    / "static_analysis.py"
)
_spec = importlib.util.spec_from_file_location("_static_analysis", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod  # dataclass field-type resolution needs this
_spec.loader.exec_module(_mod)

analyze_python = _mod.analyze_python
parse_denied_imports = _mod.parse_denied_imports

DENIED = frozenset({"ctypes", "socket", "subprocess"})


class TestAnalyzePython:
    def test_clean_code_passes(self):
        report = analyze_python("import json\nprint(json.dumps({'a': 1}))", DENIED)
        assert report.parse_ok
        assert report.flagged_imports == []

    def test_syntax_error_rejected(self):
        report = analyze_python("def broken(:\n  pass", DENIED)
        assert not report.parse_ok
        assert "invalid syntax" in (report.syntax_error or "")

    def test_direct_import_flagged(self):
        report = analyze_python("import socket\nsocket.create_connection(('a', 1))", DENIED)
        assert report.flagged_imports == ["socket"]

    def test_from_import_and_submodule_flagged_by_root(self):
        report = analyze_python(
            "from subprocess import run\nimport ctypes.util", DENIED
        )
        assert set(report.flagged_imports) == {"subprocess", "ctypes"}

    def test_dunder_import_literal_flagged(self):
        report = analyze_python("m = __import__('socket')", DENIED)
        assert report.flagged_imports == ["socket"]

    def test_importlib_literal_flagged(self):
        code = "import importlib\nm = importlib.import_module('ctypes')"
        report = analyze_python(code, DENIED)
        assert "ctypes" in report.flagged_imports

    def test_never_executes_code(self):
        # Top-level side effect must NOT run during analysis.
        report = analyze_python("open('/tmp/pwned', 'w').write('x')", DENIED)
        assert report.parse_ok
        import os

        assert not os.path.exists("/tmp/pwned")


class TestParseDeniedImports:
    def test_parses_and_strips(self):
        assert parse_denied_imports(" ctypes , socket ,,") == frozenset(
            {"ctypes", "socket"}
        )

    def test_empty_spec(self):
        assert parse_denied_imports("") == frozenset()
