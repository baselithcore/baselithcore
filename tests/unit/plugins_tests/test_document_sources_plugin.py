from core.doc_sources import DocumentSourceError as CoreDocumentSourceError
from core.doc_sources import create_document_sources as core_create_document_sources
from core.doc_sources import readers as core_readers
from plugins.document_sources import (
    DocumentSourceError,
    create_document_sources,
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


async def test_read_item_rejects_sibling_directory_sharing_the_root_prefix(
    tmp_path,
) -> None:
    """``/x/docs-private`` must not pass a containment check for ``/x/docs``."""
    from plugins.document_sources.filesystem import FilesystemDocumentSource

    root = tmp_path / "docs"
    root.mkdir()
    sibling = tmp_path / "docs-private"
    sibling.mkdir()
    secret = sibling / "secret.md"
    secret.write_text("# Secret\ncontent that must not be indexed")

    source = FilesystemDocumentSource(root=root)
    assert await source.read_item(secret) is None


async def test_read_item_rejects_symlink_escaping_the_root(tmp_path) -> None:
    from plugins.document_sources.filesystem import FilesystemDocumentSource

    root = tmp_path / "docs"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\nnot part of the corpus")
    link = root / "link.md"
    link.symlink_to(outside)

    source = FilesystemDocumentSource(root=root)
    assert await source.read_item(link) is None


def test_ooxml_guard_refuses_a_zip_bomb(tmp_path, monkeypatch) -> None:
    import sys
    import zipfile

    utils_module = sys.modules["plugins.document_sources.utils"]

    bomb = tmp_path / "bomb.docx"
    with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", b"\0" * (4 * 1024 * 1024))
    # 4 MiB of zeros deflates ~1000:1 — over the per-member ratio cap.
    assert utils_module.ooxml_archive_is_safe(bomb) is False

    monkeypatch.setattr(utils_module, "OOXML_MAX_MEMBER_RATIO", 10**9)
    monkeypatch.setattr(utils_module, "OOXML_MAX_UNCOMPRESSED_BYTES", 1024)
    assert utils_module.ooxml_archive_is_safe(bomb) is False


def test_ooxml_guard_accepts_an_ordinary_archive(tmp_path) -> None:
    import zipfile

    from plugins.document_sources.utils import ooxml_archive_is_safe

    doc = tmp_path / "ok.docx"
    with zipfile.ZipFile(doc, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", "<w:document>hello</w:document>")
    assert ooxml_archive_is_safe(doc) is True


def test_word_reader_skips_unsafe_archive(tmp_path, monkeypatch) -> None:
    import importlib

    readers_module = importlib.import_module("plugins.document_sources.readers")

    monkeypatch.setattr(readers_module, "ooxml_archive_is_safe", lambda p: False)
    target = tmp_path / "x.docx"
    target.write_bytes(b"not parsed")
    assert readers_module.read_word(target) is None
