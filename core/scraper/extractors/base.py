# core/scraper/extractors/base.py
"""Base extractor class and registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from functools import lru_cache
from typing import TYPE_CHECKING, Any, ClassVar

from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from ..models import ScrapedPage


class BaseExtractor(ABC):
    """Abstract base class for content extractors.

    Extractors are responsible for parsing HTML and extracting
    specific types of data (text, links, images, etc).
    """

    name: ClassVar[str] = "base"

    # Tags to remove before extraction (noise)
    NOISE_TAGS: ClassVar[list[str]] = [
        "script",
        "style",
        "noscript",
        "iframe",
        "svg",
        "canvas",
    ]

    def __init__(self, remove_noise: bool = True):
        """Initialize the extractor.

        Args:
            remove_noise: Whether to remove noise tags before extraction.
        """
        self.remove_noise = remove_noise

    @abstractmethod
    def extract(self, page: ScrapedPage, base_url: str | None = None) -> Any:
        """Extract data from a scraped page.

        Args:
            page: The scraped page to extract from.
            base_url: Optional base URL for resolving relative links.

        Returns:
            Extracted data (type depends on extractor).
        """
        ...

    def parse_html(self, html: str) -> BeautifulSoup:
        """Parse HTML content into BeautifulSoup.

        Args:
            html: Raw HTML content.

        Returns:
            BeautifulSoup object.
        """
        soup = BeautifulSoup(html, "html.parser")

        if self.remove_noise:
            for tag in self.NOISE_TAGS:
                for element in soup.find_all(tag):
                    element.decompose()

        return soup

    @lru_cache(maxsize=100)
    def _cached_parse(self, html: str) -> BeautifulSoup:
        """Cached HTML parsing for repeated extractions.

        Note: Use with caution as it caches by HTML content.

        Args:
            html: Raw HTML content.

        Returns:
            BeautifulSoup object.
        """
        return self.parse_html(html)


class ExtractorRegistry:
    """Registry for managing extractors.

    Provides a central place to register and retrieve extractors by name.
    """

    _extractors: ClassVar[dict[str, type[BaseExtractor]]] = {}

    @classmethod
    def register(cls, extractor_class: type[BaseExtractor]) -> type[BaseExtractor]:
        """Register an extractor class.

        Can be used as a decorator:
            @ExtractorRegistry.register
            class MyExtractor(BaseExtractor):
                name = "my_extractor"

        Args:
            extractor_class: The extractor class to register.

        Returns:
            The registered class (for decorator usage).
        """
        cls._extractors[extractor_class.name] = extractor_class
        return extractor_class

    @classmethod
    def get(cls, name: str) -> type[BaseExtractor] | None:
        """Get an extractor class by name.

        Args:
            name: The extractor name.

        Returns:
            The extractor class or None if not found.
        """
        return cls._extractors.get(name)

    @classmethod
    def create(cls, name: str, **kwargs) -> BaseExtractor | None:
        """Create an extractor instance by name.

        Args:
            name: The extractor name.
            **kwargs: Arguments to pass to the extractor constructor.

        Returns:
            Extractor instance or None if not found.
        """
        extractor_class = cls.get(name)
        if extractor_class:
            return extractor_class(**kwargs)
        return None

    @classmethod
    def list_names(cls) -> list[str]:
        """Get list of registered extractor names.

        Returns:
            List of extractor names.
        """
        return list(cls._extractors.keys())
