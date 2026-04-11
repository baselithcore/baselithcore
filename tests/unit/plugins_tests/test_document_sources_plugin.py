from core.doc_sources import create_document_sources as core_create_document_sources
from core.doc_sources import DocumentSourceError as CoreDocumentSourceError
from core.doc_sources import readers as core_readers
from plugins.document_sources import (
    create_document_sources,
    DocumentSourceError,
    readers,
)
from plugins.document_sources.plugin import DocumentSourcesPlugin


def test_core_doc_sources_alias_points_to_plugin_exports() -> None:
    assert core_create_document_sources is create_document_sources
    assert CoreDocumentSourceError is DocumentSourceError
    assert core_readers is readers


def test_document_sources_plugin_exposes_manifest_metadata() -> None:
    plugin = DocumentSourcesPlugin()

    assert plugin.metadata.name == "document-sources"
    assert "documents" in plugin.metadata.tags
