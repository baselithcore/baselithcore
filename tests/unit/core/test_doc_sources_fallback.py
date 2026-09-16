"""The optional source shim must preserve its factory call contract."""

import runpy
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

from core.utils import optional_import

SHIM = Path(__file__).resolve().parents[3] / "core" / "doc_sources" / "__init__.py"


def test_missing_plugin_accepts_space_filter(monkeypatch):
    monkeypatch.setattr(optional_import, "optional_module", Mock(return_value=None))
    namespace = runpy.run_path(str(SHIM))
    assert namespace["create_document_sources"]() == []
    assert namespace["create_document_sources"](space_filter=["tenant-a"]) == []
    assert issubclass(namespace["DocumentSourceError"], Exception)


def test_present_plugin_keeps_module_identity(monkeypatch):
    plugin = ModuleType("plugins.document_sources")
    monkeypatch.setattr(optional_import, "optional_module", Mock(return_value=plugin))
    monkeypatch.setitem(
        sys.modules, "_source_shim_test", ModuleType("_source_shim_test")
    )
    code = compile(SHIM.read_text(), str(SHIM), "exec")
    exec(code, {"__name__": "_source_shim_test"})
    assert sys.modules["_source_shim_test"] is plugin
