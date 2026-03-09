# core/scraper/extractors/metadata.py
"""Page metadata extractor."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from ..models import PageMetadata
from .base import BaseExtractor, ExtractorRegistry

if TYPE_CHECKING:
    from ..models import ScrapedPage


@ExtractorRegistry.register
class MetadataExtractor(BaseExtractor):
    """Extractor for page metadata (title, description, Open Graph, etc)."""

    name: ClassVar[str] = "metadata"

    def __init__(self, remove_noise: bool = False):
        """Initialize the metadata extractor.

        Args:
            remove_noise: Ignored for metadata extraction.
        """
        super().__init__(remove_noise=False)  # Don't remove noise for metadata

    def extract(self, page: ScrapedPage, base_url: str | None = None) -> PageMetadata:
        """Extract metadata from the page.

        Args:
            page: The scraped page.
            base_url: Ignored for metadata extraction.

        Returns:
            PageMetadata object.
        """
        if not page.html:
            return PageMetadata()

        soup = self.parse_html(page.html)

        # Extract title
        title = None
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)

        # Extract meta tags
        description = self._get_meta_content(soup, "description")
        keywords_str = self._get_meta_content(soup, "keywords")
        keywords = (
            [k.strip() for k in keywords_str.split(",") if k.strip()]
            if keywords_str
            else []
        )
        author = self._get_meta_content(soup, "author")
        robots = self._get_meta_content(soup, "robots")

        # Canonical URL
        canonical = None
        canonical_link = soup.find("link", rel="canonical")
        if canonical_link:
            can_val = canonical_link.get("href")
            if isinstance(can_val, list):
                canonical = can_val[0]
            else:
                canonical = can_val

        # Language
        language = None
        html_tag = soup.find("html")
        if html_tag:
            lang_val = html_tag.get("lang")
            if isinstance(lang_val, list):
                language = lang_val[0]
            else:
                language = lang_val

        # Open Graph
        og_title = self._get_meta_property(soup, "og:title")
        og_description = self._get_meta_property(soup, "og:description")
        og_image = self._get_meta_property(soup, "og:image")
        og_type = self._get_meta_property(soup, "og:type")

        # Twitter Card
        twitter_card = self._get_meta_content(soup, "twitter:card")
        twitter_title = self._get_meta_content(soup, "twitter:title")
        twitter_description = self._get_meta_content(soup, "twitter:description")
        twitter_image = self._get_meta_content(soup, "twitter:image")

        return PageMetadata(
            title=title,
            description=description,
            keywords=keywords,
            author=author,
            canonical_url=canonical,
            og_title=og_title,
            og_description=og_description,
            og_image=og_image,
            og_type=og_type,
            twitter_card=twitter_card,
            twitter_title=twitter_title,
            twitter_description=twitter_description,
            twitter_image=twitter_image,
            robots=robots,
            language=language,
        )

    def _get_meta_content(self, soup, name: str) -> str | None:
        """Get content from a meta tag by name.

        Args:
            soup: BeautifulSoup object.
            name: Meta tag name attribute.

        Returns:
            Content value or None.
        """
        tag = soup.find("meta", attrs={"name": name})
        if tag:
            content = tag.get("content")
            if isinstance(content, list):
                return " ".join(content)
            return content
        return None

    def _get_meta_property(self, soup, property_name: str) -> str | None:
        """Get content from a meta tag by property.

        Args:
            soup: BeautifulSoup object.
            property_name: Meta tag property attribute.

        Returns:
            Content value or None.
        """
        tag = soup.find("meta", attrs={"property": property_name})
        if tag:
            content = tag.get("content")
            if isinstance(content, list):
                return " ".join(content)
            return content
        return None
