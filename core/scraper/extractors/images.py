# core/scraper/extractors/images.py
"""Image extractor."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar
from urllib.parse import urljoin

from ..models import Image
from .base import BaseExtractor, ExtractorRegistry

if TYPE_CHECKING:
    from ..models import ScrapedPage


@ExtractorRegistry.register
class ImageExtractor(BaseExtractor):
    """Extractor for images from HTML content.

    Supports standard img tags, lazy-loaded images, and picture elements.
    """

    name: ClassVar[str] = "images"

    # Lazy loading attributes commonly used
    LAZY_ATTRS: ClassVar[list[str]] = [
        "data-src",
        "data-lazy-src",
        "data-original",
        "data-srcset",
        "loading",
    ]

    def __init__(
        self,
        remove_noise: bool = False,
        min_width: int | None = None,
        min_height: int | None = None,
        include_base64: bool = False,
    ):
        """Initialize the image extractor.

        Args:
            remove_noise: Whether to remove noise elements.
            min_width: Minimum image width to include.
            min_height: Minimum image height to include.
            include_base64: Whether to include base64 encoded images.
        """
        super().__init__(remove_noise=remove_noise)
        self.min_width = min_width
        self.min_height = min_height
        self.include_base64 = include_base64

    def extract(self, page: ScrapedPage, base_url: str | None = None) -> list[Image]:
        """Extract images from the page.

        Args:
            page: The scraped page.
            base_url: Base URL for resolving relative URLs.

        Returns:
            List of extracted Image objects.
        """
        if not page.html:
            return []

        soup = self.parse_html(page.html)
        base = base_url or page.final_url

        images: list[Image] = []
        seen_srcs: set[str] = set()

        for img in soup.find_all("img"):
            src = self._get_image_src(img)
            if not src:
                continue

            # Skip base64 if not wanted
            if src.startswith("data:") and not self.include_base64:
                continue

            # Resolve relative URLs
            if not src.startswith(("http://", "https://", "data:")):
                src = urljoin(base, src)

            # Skip duplicates
            if src in seen_srcs:
                continue
            seen_srcs.add(src)

            # Get dimensions
            width = self._parse_dimension(img.get("width"))
            height = self._parse_dimension(img.get("height"))

            # Filter by dimensions
            if self.min_width and width and width < self.min_width:
                continue
            if self.min_height and height and height < self.min_height:
                continue

            # Get alt text
            alt_val = img.get("alt", "")
            if isinstance(alt_val, list):
                alt = " ".join(alt_val)
            else:
                alt = alt_val or ""

            # Get srcset
            srcset_val = img.get("srcset") or img.get("data-srcset")
            srcset: str | None = None
            if isinstance(srcset_val, list):
                srcset = " ".join(srcset_val)
            elif isinstance(srcset_val, str):
                srcset = srcset_val

            images.append(
                Image(
                    src=src,
                    alt=alt[:500],  # Limit length
                    width=width,
                    height=height,
                    srcset=srcset,
                )
            )

        return images

    def _get_image_src(self, img) -> str | None:
        """Get the image source, handling lazy loading.

        Args:
            img: BeautifulSoup img element.

        Returns:
            Image source URL or None.
        """
        # Try standard src first
        src = img.get("src")
        if isinstance(src, list):
            src = src[0]

        if src and not src.startswith("data:image/gif"):
            return src

        # Check lazy loading attributes
        for attr in self.LAZY_ATTRS:
            value = img.get(attr)
            if isinstance(value, list):
                value = value[0]

            if value and not value.startswith("data:image/gif"):
                return value

        return src

    def _parse_dimension(self, value) -> int | None:
        """Parse a dimension value to int.

        Args:
            value: Dimension string or int.

        Returns:
            Integer value or None.
        """
        if value is None:
            return None

        if isinstance(value, int):
            return value

        try:
            # Remove 'px' suffix if present
            clean = str(value).replace("px", "").strip()
            return int(float(clean))
        except (ValueError, TypeError):
            return None
