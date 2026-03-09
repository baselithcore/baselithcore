from unittest.mock import AsyncMock, patch
import pytest
from core.doc_sources.filesystem import FilesystemDocumentSource
from core.doc_sources.web import WebDocumentSource


@pytest.fixture
def mock_root(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "test.md").write_text("# Test\nContent")
    return root


@pytest.mark.asyncio
async def test_filesystem_read_item(mock_root):
    source = FilesystemDocumentSource(root=mock_root)
    item = await source.read_item(mock_root / "test.md")
    assert item is not None
    assert item.content == "# Test\nContent"
    assert item.metadata["filename"] == "test.md"


@pytest.mark.asyncio
async def test_filesystem_iter_items(mock_root):
    source = FilesystemDocumentSource(root=mock_root)
    items = []
    async for item in source.iter_items():
        items.append(item)
    assert len(items) == 1
    assert items[0].content == "# Test\nContent"


@pytest.mark.asyncio
async def test_web_source_lifecycle():
    with patch("core.doc_sources.web.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value = mock_client
        source = WebDocumentSource(["https://example.com"])
        await source.close()
        mock_client.aclose.assert_called_once()
