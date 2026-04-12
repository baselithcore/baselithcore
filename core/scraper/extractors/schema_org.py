"""Backward-compatible shim for the Web Scraper schema.org extractor module."""

import sys

import plugins.web_scraper.extractors.schema_org as _schema_org

sys.modules[__name__] = _schema_org
