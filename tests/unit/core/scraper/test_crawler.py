from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.config.scraper import ScraperConfig
from core.scraper.crawler import CrawlEngine
from core.scraper.models import CrawlResult, ExtractedData, ScrapedPage


@pytest.fixture
def mock_config():
    config = ScraperConfig()
    config.max_depth = 2
    config.max_pages = 5
    config.follow_robots_txt = False  # Simplify by default
    return config


@pytest.fixture
def mock_scraped_page():
    return ScrapedPage(
        url="http://example.com",
        final_url="http://example.com",
        status_code=200,
        html="<html><body><a href='http://example.com/page1'>link</a></body></html>",
    )


@pytest.fixture
def mock_extracted_data():
    data = ExtractedData()
    data.links = [MagicMock(url="http://example.com/page1", nofollow=False)]
    return data


@pytest.fixture
def mock_scraper_cls():
    with patch("core.scraper.crawler.Scraper") as mock_cls:
        # Mock context manager
        mock_instance = AsyncMock()
        mock_cls.return_value = mock_instance
        mock_instance.__aenter__.return_value = mock_instance
        mock_instance.__aexit__.return_value = None
        yield mock_cls


@pytest.fixture(autouse=True)
def mock_dns_resolution():
    with patch("socket.getaddrinfo") as mock_getaddrinfo:
        # Return a non-private IP for tests
        mock_getaddrinfo.return_value = [(2, 1, 6, "", ("93.184.216.34", 0))]
        yield mock_getaddrinfo


@pytest.mark.asyncio
async def test_crawler_initialization(mock_config):
    crawler = CrawlEngine(config=mock_config)
    assert crawler.max_depth == 2
    assert crawler.max_pages == 5


@pytest.mark.asyncio
async def test_crawl_single_page(
    mock_config, mock_scraper_cls, mock_scraped_page, mock_extracted_data
):
    # Setup scraper mock
    mock_instance = mock_scraper_cls.return_value
    mock_instance.scrape.return_value = (mock_scraped_page, mock_extracted_data)

    crawler = CrawlEngine(config=mock_config)

    results = []
    async for page, data in crawler.crawl("http://example.com"):
        results.append(page)

    assert len(results) >= 1
    assert results[0].url == "http://example.com"
    mock_instance.scrape.assert_called()


@pytest.mark.asyncio
async def test_crawl_recursion(mock_config, mock_scraper_cls):
    # Setup scraper to return different pages based on URL
    mock_instance = mock_scraper_cls.return_value

    async def side_effect(url, **kwargs):
        u = url.rstrip("/")
        if u == "http://example.com":
            data = ExtractedData()
            data.links = [MagicMock(url="http://example.com/page1", nofollow=False)]
            return ScrapedPage(url=url, final_url=url, status_code=200, html=""), data
        elif u == "http://example.com/page1":
            return ScrapedPage(
                url=url, final_url=url, status_code=200, html=""
            ), ExtractedData()
        return ScrapedPage(
            url=url, status_code=404, html="", error="Not Found"
        ), ExtractedData()

    mock_instance.scrape.side_effect = side_effect

    crawler = CrawlEngine(config=mock_config, max_pages=10)

    pages = []
    async for page, _ in crawler.crawl("http://example.com"):
        pages.append(page.url)

    # Crawler normalizes URLs, so we should check for presence carefully
    # normalize_url adds trailing slash usually for domains
    print(f"DEBUG PAGES: {pages}")
    assert any(p.rstrip("/") == "http://example.com" for p in pages)
    assert any(p.rstrip("/") == "http://example.com/page1" for p in pages)
    assert len(pages) == 2


@pytest.mark.asyncio
async def test_crawl_full(
    mock_config, mock_scraper_cls, mock_scraped_page, mock_extracted_data
):
    mock_instance = mock_scraper_cls.return_value
    mock_instance.scrape.return_value = (mock_scraped_page, mock_extracted_data)

    crawler = CrawlEngine(config=mock_config, max_pages=1)

    result = await crawler.crawl_full("http://example.com")

    assert isinstance(result, CrawlResult)
    assert result.seed_url == "http://example.com"
    assert result.stats.pages_crawled == 1
    assert len(result.pages) == 1


@pytest.mark.asyncio
async def test_robots_txt_blocking(mock_config):
    mock_config.follow_robots_txt = True

    # The robots fetch now goes through the shared pooled client
    # (plugins.web_scraper._http_pool.get_robots_client) instead of building a
    # new httpx.AsyncClient per call.
    mock_client = _StreamingClient([b"User-agent: *\nDisallow: /private"])

    with (
        patch(
            "core.scraper.crawler.get_robots_client",
            new=AsyncMock(return_value=mock_client),
        ),
        patch(
            "core.scraper.crawler.parse_robots_txt",
            return_value={"disallow": ["/private"]},
        ),
        patch("core.scraper.crawler.is_url_allowed_by_robots", return_value=False),
    ):
        crawler = CrawlEngine(config=mock_config)

        count = 0
        async for _ in crawler.crawl("http://example.com/private"):
            count += 1

        assert count == 0
        # Verify robots.txt was fetched via the shared client.
        assert mock_client.calls


class _StreamingResponse:
    def __init__(self, chunks: list[bytes], status_code: int = 200) -> None:
        self.status_code = status_code
        self._chunks = chunks
        self.bytes_served = 0

    async def aiter_bytes(self):
        for chunk in self._chunks:
            self.bytes_served += len(chunk)
            yield chunk


class _StreamingClient:
    """Stand-in for the shared robots client's ``stream`` context manager."""

    def __init__(self, chunks: list[bytes], status_code: int = 200) -> None:
        self.response = _StreamingResponse(chunks, status_code)
        self.calls: list[tuple[str, str, dict]] = []

    def stream(self, method: str, url: str, **kwargs):
        from contextlib import asynccontextmanager

        self.calls.append((method, url, kwargs))

        @asynccontextmanager
        async def _cm():
            yield self.response

        return _cm()


@pytest.mark.asyncio
async def test_robots_fetch_is_pinned_to_the_verified_ip(mock_config):
    """robots.txt must go to the validated IP with Host/SNI restored, not be
    re-resolved by the client (DNS rebinding)."""
    client = _StreamingClient([b"User-agent: *\nDisallow: /x"])
    with patch(
        "core.scraper.crawler.get_robots_client", new=AsyncMock(return_value=client)
    ):
        rules = await CrawlEngine(config=mock_config)._fetch_robots(
            "http://example.com/start"
        )

    method, url, kwargs = client.calls[0]
    assert url == "http://93.184.216.34/robots.txt"
    assert kwargs["headers"] == {"Host": "example.com"}
    assert kwargs["extensions"] == {"sni_hostname": "example.com"}
    assert rules["disallow"] == ["/x"]


@pytest.mark.asyncio
async def test_robots_fetch_refuses_internal_targets(mock_config, mock_dns_resolution):
    mock_dns_resolution.return_value = [(2, 1, 6, "", ("10.0.0.5", 0))]
    client = _StreamingClient([b""])
    with patch(
        "core.scraper.crawler.get_robots_client", new=AsyncMock(return_value=client)
    ):
        rules = await CrawlEngine(config=mock_config)._fetch_robots(
            "http://intranet.example/"
        )
    assert client.calls == []
    assert rules == {"allow": [], "disallow": []}


@pytest.mark.asyncio
async def test_robots_body_is_capped(mock_config):
    from plugins.web_scraper.crawler import ROBOTS_MAX_BYTES

    chunk = b"#" * 65536
    client = _StreamingClient([chunk] * 1000)  # ~64 MB offered
    with patch(
        "core.scraper.crawler.get_robots_client", new=AsyncMock(return_value=client)
    ):
        await CrawlEngine(config=mock_config)._fetch_robots("http://example.com/")
    assert client.response.bytes_served <= ROBOTS_MAX_BYTES + len(chunk)


@pytest.mark.asyncio
async def test_repeated_links_are_enqueued_once(mock_config, mock_scraper_cls):
    """A nav link present on every page must not be re-queued per page."""
    mock_config.max_pages = 3
    mock_config.max_depth = 3
    pages = {
        "http://example.com/": ["http://example.com/a", "http://example.com/b"],
        "http://example.com/a": ["http://example.com/b", "http://example.com/"],
        "http://example.com/b": ["http://example.com/a", "http://example.com/"],
    }
    scraped: list[str] = []

    async def scrape(url, **_kwargs):
        scraped.append(url)
        data = ExtractedData()
        data.links = [MagicMock(url=u, nofollow=False) for u in pages.get(url, [])]
        return (
            ScrapedPage(url=url, final_url=url, status_code=200, html="<html/>"),
            data,
        )

    mock_scraper_cls.return_value.scrape.side_effect = scrape
    from plugins.web_scraper import crawler as crawler_module

    crawler = CrawlEngine(config=mock_config, max_pages=10, max_depth=3)
    with patch.object(
        crawler_module, "is_valid_url", wraps=crawler_module.is_valid_url
    ) as valid:
        async for _ in crawler.crawl("http://example.com/"):
            pass
    # Only the two never-seen links are considered for enqueueing; before the
    # fix every not-yet-*visited* repeat was re-checked and re-appended.
    assert valid.call_count == 2
    assert sorted(scraped) == sorted(pages)
    assert len(scraped) == len(set(scraped))
