import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from core.scraper.crawler import CrawlEngine
from core.scraper.models import ScrapedPage, ExtractedData, CrawlResult
from core.config.scraper import ScraperConfig


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

    with (
        patch("core.scraper.crawler.httpx.AsyncClient") as mock_client_cls,
        patch(
            "core.scraper.crawler.parse_robots_txt",
            return_value={"disallow": ["/private"]},
        ),
        patch("core.scraper.crawler.is_url_allowed_by_robots", return_value=False),
    ):
        crawler = CrawlEngine(config=mock_config)

        # Mock httpx response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "User-agent: *\nDisallow: /private"
        mock_client_cls.return_value.__aenter__.return_value.get.return_value = (
            mock_response
        )

        count = 0
        async for _ in crawler.crawl("http://example.com/private"):
            count += 1

        assert count == 0
        # Verify robots.txt was fetched
        mock_client_cls.return_value.__aenter__.return_value.get.assert_called()
