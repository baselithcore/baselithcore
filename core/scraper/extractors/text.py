# core/scraper/extractors/text.py
"""Text content extractor."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar

from ..utils import clean_text
from .base import BaseExtractor, ExtractorRegistry

if TYPE_CHECKING:
    from ..models import ScrapedPage


@ExtractorRegistry.register
class TextExtractor(BaseExtractor):
    """Extractor for clean text content from HTML.

    Removes navigation, headers, footers, and other non-content elements
    to extract the main textual content.
    """

    name: ClassVar[str] = "text"

    # Additional tags to remove for text extraction
    ADDITIONAL_NOISE: ClassVar[list[str]] = [
        "nav",
        "header",
        "footer",
        "aside",
        "form",
        "button",
        "input",
        "select",
        "textarea",
        "menu",
        "menuitem",
    ]

    # CSS classes/IDs that typically contain non-content
    NOISE_PATTERNS: ClassVar[list[str]] = [
        r"nav",
        r"menu",
        r"sidebar",
        r"footer",
        r"header",
        r"comment",
        r"social",
        r"share",
        r"ad-",
        r"advert",
        r"banner",
        r"popup",
        r"modal",
        r"cookie",
    ]

    def __init__(
        self,
        remove_noise: bool = True,
        min_text_length: int = 50,
        extract_main_only: bool = True,
    ):
        """Initialize the text extractor.

        Args:
            remove_noise: Whether to remove noise elements.
            min_text_length: Minimum text length to consider valid.
            extract_main_only: Try to find and extract main content only.
        """
        super().__init__(remove_noise=remove_noise)
        self.min_text_length = min_text_length
        self.extract_main_only = extract_main_only

    def extract(self, page: ScrapedPage, base_url: str | None = None) -> str | None:
        """Extract clean text from the page.

        Args:
            page: The scraped page.
            base_url: Ignored for text extraction.

        Returns:
            Cleaned text content or None if insufficient.
        """
        if not page.html:
            return None

        soup = self.parse_html(page.html)

        # Remove additional noise elements
        for tag in self.ADDITIONAL_NOISE:
            for element in soup.find_all(tag):
                element.decompose()

        # Remove elements matching noise patterns
        for pattern in self.NOISE_PATTERNS:
            regex = re.compile(pattern, re.IGNORECASE)
            for element in soup.find_all(class_=regex):
                element.decompose()
            for element in soup.find_all(id=regex):
                element.decompose()

        # Try to find main content
        main_content = None
        if self.extract_main_only:
            main_content = self._find_main_content(soup)

        if main_content:
            text = main_content.get_text(separator=" ", strip=True)
        else:
            # Fall back to body content
            body = soup.find("body")
            if body:
                text = body.get_text(separator=" ", strip=True)
            else:
                text = soup.get_text(separator=" ", strip=True)

        # Clean the text
        text = clean_text(text)

        # Check minimum length
        if len(text) < self.min_text_length:
            return None

        return text

    def _find_main_content(self, soup):
        """Try to find the main content element.

        Args:
            soup: BeautifulSoup object.

        Returns:
            Main content element or None.
        """
        # Try common main content selectors
        main_selectors = [
            "main",
            "article",
            "[role='main']",
            "#content",
            "#main",
            "#main-content",
            ".content",
            ".main",
            ".article",
            ".post-content",
            ".entry-content",
        ]

        for selector in main_selectors:
            element = soup.select_one(selector)
            if element:
                return element

        return None
