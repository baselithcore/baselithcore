# core/scraper/extractors/links.py
"""Link extractor."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar
from urllib.parse import urljoin, urlparse

from ..models import Link
from ..utils import is_blocked_extension, is_valid_url, normalize_url
from .base import BaseExtractor, ExtractorRegistry

if TYPE_CHECKING:
    from ..models import ScrapedPage


@ExtractorRegistry.register
class LinkExtractor(BaseExtractor):
    """Extractor for links from HTML content.

    Extracts all anchor tags and categorizes them as internal/external.
    """

    name: ClassVar[str] = "links"

    def __init__(
        self,
        remove_noise: bool = False,  # Keep all links by default
        include_external: bool = True,
        filter_blocked_extensions: bool = True,
        respect_nofollow: bool = True,
    ):
        """Initialize the link extractor.

        Args:
            remove_noise: Whether to remove noise elements.
            include_external: Whether to include external links.
            filter_blocked_extensions: Skip links with blocked file extensions.
            respect_nofollow: Honor nofollow attributes.
        """
        super().__init__(remove_noise=remove_noise)
        self.include_external = include_external
        self.filter_blocked_extensions = filter_blocked_extensions
        self.respect_nofollow = respect_nofollow

    def extract(self, page: ScrapedPage, base_url: str | None = None) -> list[Link]:
        """Extract links from the page.

        Args:
            page: The scraped page.
            base_url: Base URL for resolving relative links.

        Returns:
            List of extracted Link objects.
        """
        if not page.html:
            return []

        soup = self.parse_html(page.html)
        base = base_url or page.final_url
        base_domain = urlparse(base).netloc.lower()

        links: list[Link] = []
        seen_urls: set[str] = set()

        for anchor in soup.find_all("a", href=True):
            href_val = anchor.get("href", "")
            href: str | None
            if isinstance(href_val, list):
                href = href_val[0]
            else:
                href = href_val

            if not href:
                continue

            # Ensure href is string
            if not isinstance(href, str):
                continue

            # Skip javascript links, mailto, tel, etc
            if href.startswith(("javascript:", "mailto:", "tel:", "#", "data:")):
                continue

            # Resolve relative URLs
            try:
                absolute_url = urljoin(base, href)
            except Exception:
                continue  # nosec B112

            # Validate URL
            if not is_valid_url(absolute_url):
                continue

            # Normalize URL
            normalized = normalize_url(absolute_url, base)

            # Skip duplicates
            if normalized in seen_urls:
                continue
            seen_urls.add(normalized)

            # Check blocked extensions
            if self.filter_blocked_extensions and is_blocked_extension(normalized):
                continue

            # Determine if internal
            link_domain = urlparse(normalized).netloc.lower()
            is_internal = link_domain == base_domain

            # Skip external if not wanted
            if not is_internal and not self.include_external:
                continue

            # Check nofollow
            rel_raw = anchor.get("rel")
            rel_list: list[str] = []

            if isinstance(rel_raw, list):
                # Filter to ensure strings
                rel_list = [str(r) for r in rel_raw]
            elif isinstance(rel_raw, str):
                rel_list = rel_raw.split()

            has_nofollow = "nofollow" in rel_list

            # Get link text
            text = anchor.get_text(strip=True) or ""

            links.append(
                Link(
                    url=normalized,
                    text=text[:500],  # Limit text length
                    rel=" ".join(rel_list) if rel_list else None,
                    is_internal=is_internal,
                    nofollow=has_nofollow,
                )
            )

        return links
