"""Tests for Scraper FilesystemStorage."""

import pytest
import json
from datetime import datetime
from core.scraper.storage.filesystem import FilesystemStorage
from core.scraper.models import ScrapedPage, ExtractedData


@pytest.fixture
def storage_path(tmp_path):
    path = tmp_path / "scraper_data"
    yield path
    # Cleanup if needed (tmp_path is auto-cleaned by pytest)


@pytest.fixture
def storage(storage_path):
    return FilesystemStorage(base_path=storage_path)


@pytest.fixture
def sample_data():
    page = ScrapedPage(
        url="http://example.com",
        final_url="http://example.com",
        status_code=200,
        html="<html></html>",
        fetched_at=datetime.now(),
        fetch_time_ms=100,
    )
    from core.scraper.models import PageMetadata

    data = ExtractedData(
        text="Hello", metadata=PageMetadata(title="Test"), images=[], links=[]
    )
    return page, data


@pytest.mark.asyncio
async def test_init(storage_path):
    storage = FilesystemStorage(base_path=storage_path)
    assert storage.base_path == storage_path
    assert storage.base_path.exists()


@pytest.mark.asyncio
async def test_save_and_exists(storage, sample_data):
    page, data = sample_data
    url = "http://example.com"

    await storage.save(url, page, data)
    assert await storage.exists(url)

    # Verify file content
    path = storage._url_to_path(url)
    assert path.exists()
    content = json.loads(path.read_text())
    assert content["url"] == url
    assert content["data"]["text"] == "Hello"


@pytest.mark.asyncio
async def test_load(storage, sample_data):
    page, data = sample_data
    url = "http://example.com"

    await storage.save(url, page, data)

    loaded = await storage.load(url)
    assert loaded is not None
    loaded_page, loaded_data = loaded

    # Note: load returns dicts currently as per implementation
    assert loaded_page["url"] == url
    assert loaded_data["text"] == "Hello"


@pytest.mark.asyncio
async def test_load_nonexistent(storage):
    assert await storage.load("http://nonexistent.com") is None


@pytest.mark.asyncio
async def test_delete(storage, sample_data):
    page, data = sample_data
    url = "http://example.com"

    await storage.save(url, page, data)
    assert await storage.exists(url)

    assert await storage.delete(url)
    assert not await storage.exists(url)

    # Delete nonexistent
    assert not await storage.delete(url)


@pytest.mark.asyncio
async def test_clear(storage, sample_data):
    page, data = sample_data
    await storage.save("http://example.com/1", page, data)
    await storage.save("http://example.com/2", page, data)

    assert len(list(storage.base_path.glob("*.json"))) == 2

    await storage.clear()
    assert len(list(storage.base_path.glob("*.json"))) == 0


@pytest.mark.asyncio
async def test_list_urls(storage, sample_data):
    page, data = sample_data
    urls = ["http://example.com/1", "http://example.com/2"]

    for url in urls:
        await storage.save(url, page, data)

    saved_urls = await storage.list_urls()
    assert len(saved_urls) == 2
    assert set(saved_urls) == set(urls)


@pytest.mark.asyncio
async def test_load_corrupt_file(storage, sample_data):
    url = "http://example.com"
    page, data = sample_data
    await storage.save(url, page, data)

    # Corrupt the file
    path = storage._url_to_path(url)
    path.write_text("{invalid json")

    assert await storage.load(url) is None
