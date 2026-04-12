"""Backward-compatible shim for the Web Scraper logging middleware module."""

import sys

import plugins.web_scraper.middleware.logging as _logging

sys.modules[__name__] = _logging
