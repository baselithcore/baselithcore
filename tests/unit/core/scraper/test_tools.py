from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from core.scraper.tools import (
    web_scrape,
    web_crawl,
    extract_structured_data,
    SCRAPER_TOOLS,
)

# --- Fixtures ---


@pytest.fixture
def mock_extracted_data():
    data = MagicMock()
    data.text = "Mocked page content"
    data.links = [
        MagicMock(url="http://example.com/1", text="Link 1", is_internal=True),
        MagicMock(url="http://external.com", text="External", is_internal=False),
    ]
    data.images = [MagicMock(src="img.jpg", alt="An image")]
    data.metadata = MagicMock(
        title="Page Title",
        description="Page desc",
        keywords="a,b,c",
        og_title="OG Title",
        og_description="OG Desc",
        og_image="og.jpg",
    )
    data.schema_org = [{"@type": "Thing", "name": "Test"}]
    data.custom = {"price": "10.00"}
    return data


@pytest.fixture
def mock_scraped_page(mock_extracted_data):
    page = MagicMock()
    page.final_url = "http://example.com/final"
    page.status_code = 200
    page.is_success = True
    page.error = None
    page.url = "http://example.com"
    return page


@pytest.fixture
def mock_scraper(mock_scraped_page, mock_extracted_data):
    scraper = AsyncMock()
    scraper.__aenter__.return_value = scraper
    scraper.__aexit__.return_value = None

    # scrape returns (Page, ExtractedData)
    scraper.scrape.return_value = (mock_scraped_page, mock_extracted_data)
    scraper._extractors = {}
    return scraper


@pytest.fixture
def mock_crawl_result(mock_scraped_page, mock_extracted_data):
    result = MagicMock()
    result.seed_url = "http://example.com"
    result.stats.pages_crawled = 5
    result.stats.pages_failed = 0
    result.stats.duration_seconds = 1.5

    # Pages handled in result.pages
    result.pages = [mock_scraped_page]

    # Map url -> extracted data
    result.extracted = {mock_scraped_page.url: mock_extracted_data}

    return result


@pytest.fixture
def mock_crawler(mock_crawl_result):
    crawler = AsyncMock()
    crawler.crawl_full.return_value = mock_crawl_result
    return crawler


# --- Tests ---


@pytest.mark.asyncio
async def test_web_scrape_defaults(mock_scraper):
    with patch("core.scraper.tools.Scraper", return_value=mock_scraper):
        result = await web_scrape("http://example.com")

        mock_scraper.scrape.assert_called_once()
        args = mock_scraper.scrape.call_args[1]
        assert args["url"] == "http://example.com"
        assert "text" in args["extractors"]
        assert "links" in args["extractors"]
        assert "metadata" in args["extractors"]

        assert result["url"] == "http://example.com/final"
        assert result["success"] is True
        assert result["text"] == "Mocked page content"
        assert len(result["links"]) == 2
        assert result["metadata"]["title"] == "Page Title"
        assert "images" not in result


@pytest.mark.asyncio
async def test_web_scrape_full(mock_scraper):
    with patch("core.scraper.tools.Scraper", return_value=mock_scraper):
        result = await web_scrape(
            "http://example.com", extract=["images", "schema_org"], use_javascript=True
        )

        args = mock_scraper.scrape.call_args[1]
        assert args["use_js"] is True
        assert args["extractors"] == ["images", "schema_org"]

        assert "text" not in result
        assert len(result["images"]) == 1
        assert len(result["schema_org"]) == 1


@pytest.mark.asyncio
async def test_web_crawl(mock_crawler):
    with patch("core.scraper.tools.CrawlEngine", return_value=mock_crawler) as mock_cls:
        try:
            result = await web_crawl("http://example.com", max_pages=5)
            # print(f"DEBUG: result={result}")
            # print(f"DEBUG: crawl_full calls={mock_crawler.crawl_full.call_count}")
        except Exception as e:
            pytest.fail(f"web_crawl raised exception: {e}")

        # Basic assertions
        assert mock_cls.called, "CrawlEngine not instantiated"
        assert mock_crawler.crawl_full.called, "crawl_full not called"
        assert result["seed_url"] == "http://example.com"


@pytest.mark.asyncio
async def test_extract_structured_data(mock_scraper):
    with patch("core.scraper.tools.Scraper", return_value=mock_scraper):
        # We also need to patch the CssSelectorExtractor import locally if we were strict,
        # but since we are mocking Scraper instance behavior, we verify logic.

        with patch("core.scraper.extractors.CssSelectorExtractor") as mock_css_cls:
            mock_css_instance = MagicMock()
            mock_css_cls.return_value = mock_css_instance

            selectors = {"price": ".price"}
            result = await extract_structured_data(
                "http://example.com", selectors=selectors
            )

            assert mock_css_cls.called
            _ = mock_css_cls.call_args[1]["schema"]
            # We can't easily check schema content without exposing it, but we know it was created

            mock_scraper.scrape.assert_called_once()
            assert "css_selector" in mock_scraper.scrape.call_args[1]["extractors"]

            assert result["success"] is True
            assert result["custom"] == {"price": "10.00"}
            assert len(result["schema_org"]) == 1


def test_scraper_tools_export():
    assert len(SCRAPER_TOOLS) == 3
    tool_names = [t["name"] for t in SCRAPER_TOOLS]
    assert "web_scrape" in tool_names
    assert "web_crawl" in tool_names
    assert "extract_structured_data" in tool_names
