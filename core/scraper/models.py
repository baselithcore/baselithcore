"""Backward-compatible shim for the Web Scraper models module."""

import sys

import plugins.web_scraper.models as _models

sys.modules[__name__] = _models
