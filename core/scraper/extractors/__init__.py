# core/scraper/extractors/__init__.py
"""Content extractors for the web scraper module."""

from .base import BaseExtractor, ExtractorRegistry
from .css_selector import CssSelectorExtractor, ExtractionSchema, FieldSchema
from .images import ImageExtractor
from .links import LinkExtractor
from .metadata import MetadataExtractor
from .schema_org import SchemaOrgExtractor
from .text import TextExtractor

__all__ = [
    "BaseExtractor",
    "ExtractorRegistry",
    "TextExtractor",
    "LinkExtractor",
    "ImageExtractor",
    "MetadataExtractor",
    "SchemaOrgExtractor",
    "CssSelectorExtractor",
    "ExtractionSchema",
    "FieldSchema",
]
