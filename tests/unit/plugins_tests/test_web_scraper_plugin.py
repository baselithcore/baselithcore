from core.scraper import CrawlEngine as CoreCrawlEngine
from core.scraper import Scraper as CoreScraper
from plugins.web_scraper import CrawlEngine, Scraper
from plugins.web_scraper.plugin import WebScraperPlugin


def test_core_scraper_alias_points_to_plugin_exports() -> None:
    assert CoreScraper is Scraper
    assert CoreCrawlEngine is CrawlEngine


def test_web_scraper_plugin_exposes_manifest_metadata() -> None:
    plugin = WebScraperPlugin()

    assert plugin.metadata.name == "web-scraper"
    assert "scraper" in plugin.metadata.tags


async def test_scraper_context_exit_keeps_the_shared_robots_client() -> None:
    """Leaving a ``with Scraper()`` block must not close the process-wide
    robots client under a concurrent crawl (its robots fetch then failed and
    the crawl ran with no robots rules at all)."""
    from unittest.mock import AsyncMock, patch

    with patch("plugins.web_scraper._http_pool.close_robots_client") as robots:
        async with Scraper() as scraper:
            scraper.close_fetchers = AsyncMock()  # type: ignore[method-assign]
        scraper.close_fetchers.assert_awaited_once()
        robots.assert_not_called()


async def test_plugin_shutdown_closes_the_shared_robots_client() -> None:
    from unittest.mock import AsyncMock, patch

    with patch(
        "plugins.web_scraper._http_pool.close_robots_client", new=AsyncMock()
    ) as robots:
        await WebScraperPlugin().shutdown()
    robots.assert_awaited_once()


async def test_extraction_runs_off_the_event_loop() -> None:
    """HTML parsing of up to 10 MB must not run on the loop thread."""
    import threading
    from unittest.mock import AsyncMock, patch

    from plugins.web_scraper.models import ExtractedData, ScrapedPage

    loop_thread = threading.get_ident()
    threads: list[int] = []

    def fake_extract(self, page, base_url, names):
        threads.append(threading.get_ident())
        return ExtractedData()

    page = ScrapedPage(
        url="https://x.test/",
        final_url="https://x.test/",
        status_code=200,
        html="<p>x</p>",
    )
    scraper = Scraper(use_cache=False, use_rate_limiter=False, use_logging=False)
    fetcher = AsyncMock()
    fetcher.fetch.return_value = page
    with (
        patch.object(Scraper, "_extract", fake_extract),
        patch.object(Scraper, "_get_fetcher", AsyncMock(return_value=fetcher)),
    ):
        await scraper.scrape("https://x.test/")
    assert threads and threads[0] != loop_thread
