"""Backward-compatible shim for the Web Scraper utilities module."""

import sys

import plugins.web_scraper.utils as _utils
from plugins.web_scraper.utils import (
    check_ssrf_safe,
    clean_text,
    extract_domain,
    get_pinned_url_for_host,
    is_blocked_extension,
    is_private_ip,
    is_same_domain,
    is_url_allowed_by_robots,
    is_valid_url,
    normalize_url,
    parse_robots_txt,
    resolve_safe_ips,
)

# Register self as the plugin module for runtime compatibility
sys.modules[__name__] = _utils

__all__ = [
    "check_ssrf_safe",
    "clean_text",
    "extract_domain",
    "get_pinned_url_for_host",
    "is_blocked_extension",
    "is_private_ip",
    "is_same_domain",
    "is_url_allowed_by_robots",
    "is_valid_url",
    "normalize_url",
    "parse_robots_txt",
    "resolve_safe_ips",
]
