import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from core.scraper.scraper import Scraper
from core.scraper.models import ScrapedPage
from core.config.scraper import ScraperConfig


@pytest.fixture
def mock_config():
    config = ScraperConfig()
    config.rate_limit_enabled = False
    return config


@pytest.fixture
def mock_fetcher():
    fetcher = AsyncMock()
    page = ScrapedPage(
        url="http://example.com",
        final_url="http://example.com",
        status_code=200,
        html="<html><body><h1>Test</h1></body></html>",
    )
    fetcher.fetch.return_value = page

    async def fetch_many_gen(urls, concurrency):
        for url in urls:
            p = ScrapedPage(
                url=url,
                final_url=url,
                status_code=200,
                html=f"<html>{url}</html>",
            )
            yield p

    fetcher.fetch_many = fetch_many_gen
    return fetcher


@pytest.fixture
def mock_middleware_chain():
    chain = MagicMock()
    chain.process_request = AsyncMock(side_effect=lambda url: url)
    chain.process_response = AsyncMock(side_effect=lambda url, page: page)
    return chain


@pytest.mark.asyncio
async def test_scraper_initialization():
    scraper = Scraper()
    assert scraper.config is not None
    assert scraper.middleware is not None
    await scraper.close()


@pytest.mark.asyncio
async def test_scrape_success(mock_config, mock_fetcher, mock_middleware_chain):
    with (
        patch("core.scraper.scraper.HttpxFetcher", return_value=mock_fetcher),
        patch(
            "core.scraper.scraper.MiddlewareChain", return_value=mock_middleware_chain
        ),
    ):
        async with Scraper(config=mock_config) as scraper:
            # Force httpx fetcher creation
            await scraper._get_fetcher(use_js=False)
            # Inject instantiated fetcher
            scraper._httpx_fetcher = mock_fetcher

            page, data = await scraper.scrape("http://example.com")

            assert page.url == "http://example.com"
            assert page.status_code == 200

            # Verify flow
            mock_middleware_chain.process_request.assert_called_with(
                "http://example.com"
            )
            mock_fetcher.fetch.assert_called_with("http://example.com")
            mock_middleware_chain.process_response.assert_called()


@pytest.mark.asyncio
async def test_scrape_cache_hit(mock_config):
    # Setup cache middleware mock
    mock_cache = MagicMock()
    cached_page = ScrapedPage(
        url="http://example.com",
        final_url="http://example.com",
        status_code=200,
        html="cached",
    )
    mock_cache.get_cached.return_value = cached_page

    with patch("core.scraper.scraper.CacheMiddleware", return_value=mock_cache):
        async with Scraper(config=mock_config, use_cache=True) as scraper:
            # Manually inject cache into middleware chain if needed,
            # but constructor does add it if use_cache=True

            page, data = await scraper.scrape("http://example.com")

            assert page.html == "cached"
            # Verify fetcher was NOT called (we can verify by checking no fetcher instantiated or called)
            assert scraper._httpx_fetcher is None
            assert scraper._playwright_fetcher is None


@pytest.mark.asyncio
async def test_scrape_blocked_by_middleware(mock_config, mock_middleware_chain):
    # Simulate blocked request by returning None from process_request
    # We must clear the side_effect from the fixture first
    mock_middleware_chain.process_request.side_effect = None
    mock_middleware_chain.process_request.return_value = None

    with (
        patch("core.scraper.scraper.HttpxFetcher") as mock_fetcher_cls,
        patch(
            "core.scraper.scraper.MiddlewareChain", return_value=mock_middleware_chain
        ),
    ):
        # Mock the fetcher instance
        mock_instance = AsyncMock()
        mock_fetcher_cls.return_value = mock_instance

        async with Scraper(config=mock_config) as scraper:
            # Ensure the scraper uses our mocked fetcher type or instance
            scraper._httpx_fetcher = mock_instance

            page, data = await scraper.scrape("http://blocked.com")

            assert page.error == "Request blocked by middleware"
            assert page.html == ""


@pytest.mark.asyncio
async def test_get_fetcher_selection(mock_config):
    async with Scraper(config=mock_config) as scraper:
        # Default (httpx)
        with patch("core.scraper.scraper.HttpxFetcher") as mock_httpx:
            mock_httpx.return_value = AsyncMock()
            f1 = await scraper._get_fetcher(use_js=False)
            assert f1 is not None

        # JS (playwright)
        with patch("core.scraper.scraper.PlaywrightFetcher") as mock_pw_cls:
            mock_pw_cls.return_value = AsyncMock()  # Ensure it returns an async-compatible mock if needed, though constructor isn't awaited

            f2 = await scraper._get_fetcher(use_js=True)
            mock_pw_cls.assert_called_once()
            assert f2 == mock_pw_cls.return_value


@pytest.mark.asyncio
async def test_scrape_many(mock_config, mock_fetcher, mock_middleware_chain):
    with (
        patch("core.scraper.scraper.HttpxFetcher", return_value=mock_fetcher),
        patch(
            "core.scraper.scraper.MiddlewareChain", return_value=mock_middleware_chain
        ),
    ):
        async with Scraper(config=mock_config) as scraper:
            scraper._httpx_fetcher = mock_fetcher

            urls = ["http://a.com", "http://b.com"]
            results = []
            async for item in scraper.scrape_many(urls):
                results.append(item)

            assert len(results) == 2
            assert results[0][0].url == "http://a.com"
