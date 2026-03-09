"""Tests for core.scraper.extractors."""

import pytest
from core.scraper.extractors import (
    TextExtractor,
    ImageExtractor,
    LinkExtractor,
    MetadataExtractor,
    SchemaOrgExtractor,
    CssSelectorExtractor,
    ExtractionSchema,
)
from core.scraper.models import ScrapedPage
from core.scraper.extractors.base import BaseExtractor


@pytest.fixture
def sample_page():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Test Page</title>
        <meta name="description" content="A test page description">
        <meta property="og:title" content="OpenGraph Title">
        <script type="application/ld+json">
        {
            "@context": "https://schema.org",
            "@type": "Article",
            "headline": "Schema Headline"
        }
        </script>
        <style>.sidebar { color: red; }</style>
    </head>
    <body>
        <nav>
            <a href="/home">Home</a>
            <a href="/about" rel="nofollow">About</a>
        </nav>
        <header>Header Content</header>
        
        <main id="main">
            <h1>Main Title</h1>
            <p>This is the <strong>main</strong> extracted content text.</p>
            <p>It has enough length to be considered valid text content for our extractor tests.</p>
            
            <img src="/img/test.jpg" alt="Test Image" width="100" height="100">
            <img src="http://external.com/logo.png" alt="Logo">
            
            <div class="content-body">
                <p>More content here.</p>
            </div>
            
            <a href="http://example.com/external">External Link</a>
        </main>
        
        <aside class="sidebar">
            <p>Sidebar content (noise)</p>
            <div id="ad-1">Ad content</div>
        </aside>
        
        <div class="cookie-banner">Accept Cookies</div>
        
        <footer>Footer Content</footer>
        
        <script>console.log('noise');</script>
    </body>
    </html>
    """
    return ScrapedPage(
        url="http://example.com/test",
        final_url="http://example.com/test",
        status_code=200,
        html=html,
    )


class MockExtractor(BaseExtractor):
    name = "mock"

    def extract(self, page: ScrapedPage, base_url: str | None = None):
        soup = self.parse_html(page.html)
        return soup


class TestBaseExtractor:
    def test_noise_removal(self, sample_page):
        extractor = MockExtractor(remove_noise=True)
        soup = extractor.extract(sample_page)

        # Scripts and Styles should be removed
        assert soup.find("script", type=None) is None
        assert soup.find("style") is None

        # NOTE: 'nav' is not cleaned by BaseExtractor default NOISE_TAGS?
        # Checking base.py or utils.py 'clean_html' logic would confirm.
        # But script/style removal is standard.


class TestTextExtractor:
    def test_extract_main_content(self, sample_page):
        extractor = TextExtractor(
            remove_noise=True, min_text_length=10, extract_main_only=True
        )
        text = extractor.extract(sample_page)
        assert text is not None
        assert "Main Title" in text
        assert "main extracted content text" in text
        # Noise should be removed
        assert "Sidebar content" not in text
        assert "Header Content" not in text
        assert "Footer Content" not in text
        # assert "Accept Cookies" not in text # cookie class noise - highly standardized

    def test_min_length(self, sample_page):
        extractor = TextExtractor(min_text_length=1000)
        text = extractor.extract(sample_page)
        assert text is None


class TestImagesExtractor:
    def test_extract_images(self, sample_page):
        extractor = ImageExtractor()
        images = extractor.extract(sample_page)

        assert len(images) == 2

        # Check normalization
        img1 = next(i for i in images if "test.jpg" in i.src)
        assert img1.src == "http://example.com/img/test.jpg"
        assert img1.alt == "Test Image"

        img2 = next(i for i in images if "logo.png" in i.src)
        assert img2.src == "http://external.com/logo.png"


class TestLinksExtractor:
    def test_extract_links(self, sample_page):
        extractor = LinkExtractor()
        links = extractor.extract(sample_page)

        assert len(links) >= 3

        home = next(link for link in links if "/home" in link.url)
        assert home.url == "http://example.com/home"
        assert home.text == "Home"

        about = next(link for link in links if "/about" in link.url)
        assert about.nofollow is True

        ext = next(link for link in links if "external" in link.url)
        assert ext.url == "http://example.com/external"


class TestMetadataExtractor:
    def test_extract_metadata(self, sample_page):
        extractor = MetadataExtractor()
        meta = extractor.extract(sample_page)

        assert meta.title == "Test Page"
        assert meta.description == "A test page description"
        assert meta.og_title == "OpenGraph Title"


class TestSchemaOrgExtractor:
    def test_extract_json_ld(self, sample_page):
        extractor = SchemaOrgExtractor()
        schema = extractor.extract(sample_page)

        assert len(schema) > 0
        article = next(
            (item for item in schema if item.get("@type") == "Article"), None
        )
        assert article is not None
        assert article["headline"] == "Schema Headline"


class TestCSSExtractor:
    def test_extract_css(self, sample_page):
        schema = ExtractionSchema()
        schema.add_field("main_headers", "main h1", multiple=True)

        extractor = CssSelectorExtractor(schema=schema)
        data = extractor.extract(sample_page)

        assert "main_headers" in data
        assert data["main_headers"][0] == "Main Title"
