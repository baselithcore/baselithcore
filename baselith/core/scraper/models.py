"""Backward-compatible shim for the Web Scraper models module."""

import sys

import baselith.plugins.web_scraper.models as _models

sys.modules[__name__] = _models
