"""Importing the document sources package registers its built-in sources.

The filesystem and web sources register with the registry at import time, and
nothing imported them: ``create_document_sources()`` came back empty and every
``/reindex`` logged "No active document sources" over a populated
DOCUMENTS_ROOT.
"""

from __future__ import annotations

import pytest

from baselith.plugins import document_sources
from baselith.plugins.document_sources.registry import registered_sources


def test_builtin_sources_are_registered_by_the_package() -> None:
    assert document_sources is not None
    assert {"filesystem", "web"} <= set(registered_sources())


def test_filesystem_source_is_created_through_the_core_shim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WEB_DOCUMENTS_ENABLED", raising=False)
    from baselith.core.doc_sources import create_document_sources

    assert [
        name for name, _ in create_document_sources(space_filter=["filesystem"])
    ] == ["filesystem"]
