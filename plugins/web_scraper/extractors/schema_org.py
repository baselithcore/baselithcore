# core/scraper/extractors/schema_org.py
"""Schema.org structured data extractor."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, ClassVar

from .base import BaseExtractor, ExtractorRegistry

if TYPE_CHECKING:
    from ..models import ScrapedPage


@ExtractorRegistry.register
class SchemaOrgExtractor(BaseExtractor):
    """Extractor for Schema.org structured data.

    Supports JSON-LD, Microdata, and RDFa formats.
    """

    name: ClassVar[str] = "schema_org"

    def __init__(self, remove_noise: bool = False):
        """Initialize the Schema.org extractor.

        Args:
            remove_noise: Ignored for schema extraction.
        """
        super().__init__(remove_noise=False)

    def extract(
        self, page: ScrapedPage, base_url: str | None = None
    ) -> list[dict[str, Any]]:
        """Extract Schema.org structured data.

        Args:
            page: The scraped page.
            base_url: Ignored for schema extraction.

        Returns:
            List of parsed Schema.org objects.
        """
        if not page.html:
            return []

        soup = self.parse_html(page.html)
        schemas: list[dict[str, Any]] = []

        # Extract JSON-LD
        schemas.extend(self._extract_json_ld(soup))

        # Extract Microdata (simplified)
        schemas.extend(self._extract_microdata(soup))

        return schemas

    def _extract_json_ld(self, soup) -> list[dict[str, Any]]:
        """Extract JSON-LD structured data.

        Args:
            soup: BeautifulSoup object.

        Returns:
            List of parsed JSON-LD objects.
        """
        schemas = []

        for script in soup.find_all("script", type="application/ld+json"):
            try:
                content = script.string
                if not content:
                    continue

                # Clean up the JSON
                content = content.strip()
                content = re.sub(r"[\n\r\t]", " ", content)

                data = json.loads(content)

                # Handle both single objects and arrays
                if isinstance(data, list):
                    schemas.extend(data)
                else:
                    schemas.append(data)

            except (json.JSONDecodeError, TypeError):
                continue

        return schemas

    def _extract_microdata(self, soup) -> list[dict[str, Any]]:
        """Extract Microdata structured data (simplified).

        Args:
            soup: BeautifulSoup object.

        Returns:
            List of parsed Microdata objects.
        """
        schemas = []

        # Find elements with itemscope
        for item in soup.find_all(itemscope=True):
            schema = self._parse_microdata_item(item)
            if schema:
                schemas.append(schema)

        return schemas

    def _parse_microdata_item(self, element) -> dict[str, Any] | None:
        """Parse a single Microdata item.

        Args:
            element: BeautifulSoup element with itemscope.

        Returns:
            Parsed item as dict or None.
        """
        item_type = element.get("itemtype")
        if not item_type:
            return None

        schema: dict[str, Any] = {"@type": item_type}

        # Find properties within this scope
        for prop in element.find_all(itemprop=True):
            # Skip nested itemscopes
            if prop.find_parent(itemscope=True) != element:
                continue

            prop_name = prop.get("itemprop")
            if not prop_name:
                continue

            # Get property value
            if prop.get("itemscope"):
                # Nested item
                value = self._parse_microdata_item(prop)
            elif prop.name == "meta":
                value = prop.get("content")
            elif prop.name in ("audio", "embed", "iframe", "img", "source", "video"):
                value = prop.get("src")
            elif prop.name in ("a", "area", "link"):
                value = prop.get("href")
            elif prop.name == "time":
                value = prop.get("datetime") or prop.get_text(strip=True)
            else:
                value = prop.get_text(strip=True)

            if value:
                # Handle multiple values for same property
                if prop_name in schema:
                    existing = schema[prop_name]
                    if isinstance(existing, list):
                        existing.append(value)
                    else:
                        schema[prop_name] = [existing, value]
                else:
                    schema[prop_name] = value

        return schema if len(schema) > 1 else None
