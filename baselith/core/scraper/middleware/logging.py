"""Backward-compatible shim for the Web Scraper logging middleware module."""

import sys

import baselith.plugins.web_scraper.middleware.logging as _logging

sys.modules[__name__] = _logging
