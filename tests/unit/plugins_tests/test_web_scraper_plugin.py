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
