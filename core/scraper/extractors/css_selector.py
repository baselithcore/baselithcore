# core/scraper/extractors/css_selector.py
"""CSS selector-based custom extractor."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from .base import BaseExtractor, ExtractorRegistry

if TYPE_CHECKING:
    from ..models import ScrapedPage


@dataclass
class FieldSchema:
    """Schema for a single field to extract."""

    name: str
    selector: str
    attribute: str | None = None  # None means get text content
    multiple: bool = False
    default: Any = None


@dataclass
class ExtractionSchema:
    """Schema for extracting multiple fields."""

    fields: list[FieldSchema] = field(default_factory=list)

    def add_field(
        self,
        name: str,
        selector: str,
        attribute: str | None = None,
        multiple: bool = False,
        default: Any = None,
    ) -> ExtractionSchema:
        """Add a field to the schema.

        Args:
            name: Field name in output.
            selector: CSS selector.
            attribute: Attribute to extract (None for text).
            multiple: Extract all matches vs first.
            default: Default value if not found.

        Returns:
            Self for chaining.
        """
        self.fields.append(
            FieldSchema(
                name=name,
                selector=selector,
                attribute=attribute,
                multiple=multiple,
                default=default,
            )
        )
        return self


def create_schema() -> ExtractionSchema:
    """Create a new extraction schema.

    Returns:
        Empty ExtractionSchema.
    """
    return ExtractionSchema()


@ExtractorRegistry.register
class CssSelectorExtractor(BaseExtractor):
    """Custom extractor using CSS selectors.

    Allows defining a schema of fields to extract based on CSS selectors.
    """

    name: ClassVar[str] = "css_selector"

    def __init__(
        self,
        schema: ExtractionSchema | None = None,
        remove_noise: bool = False,
    ):
        """Initialize the CSS selector extractor.

        Args:
            schema: Extraction schema defining fields to extract.
            remove_noise: Whether to remove noise elements.
        """
        super().__init__(remove_noise=remove_noise)
        self.schema = schema or ExtractionSchema()

    def extract(self, page: ScrapedPage, base_url: str | None = None) -> dict[str, Any]:
        """Extract data using CSS selectors.

        Args:
            page: The scraped page.
            base_url: Ignored for CSS extraction.

        Returns:
            Dict with extracted field values.
        """
        if not page.html:
            return {}

        soup = self.parse_html(page.html)
        result: dict[str, Any] = {}

        for field_schema in self.schema.fields:
            try:
                if field_schema.multiple:
                    elements = soup.select(field_schema.selector)
                    values = []
                    for el in elements:
                        value = self._extract_value(el, field_schema.attribute)
                        if value:
                            values.append(value)
                    result[field_schema.name] = (
                        values if values else field_schema.default
                    )
                else:
                    element = soup.select_one(field_schema.selector)
                    if element:
                        result[field_schema.name] = self._extract_value(
                            element, field_schema.attribute
                        )
                    else:
                        result[field_schema.name] = field_schema.default
            except Exception:
                result[field_schema.name] = field_schema.default

        return result

    def _extract_value(self, element, attribute: str | None) -> str | None:
        """Extract value from element.

        Args:
            element: BeautifulSoup element.
            attribute: Attribute to extract or None for text.

        Returns:
            Extracted value.
        """
        if attribute:
            return element.get(attribute)
        return element.get_text(strip=True)

    def with_schema(self, schema: ExtractionSchema) -> CssSelectorExtractor:
        """Create a new extractor with the given schema.

        Args:
            schema: Extraction schema.

        Returns:
            New extractor instance.
        """
        return CssSelectorExtractor(schema=schema, remove_noise=self.remove_noise)
