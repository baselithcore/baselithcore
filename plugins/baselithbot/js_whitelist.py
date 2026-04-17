"""Whitelist of JavaScript snippets allowed for in-page execution.

The OpenClaw-style ``eval_js_safe`` tool MUST only execute snippets defined
here. User-supplied arguments are sanitized via
``core.services.sanitization.InputSanitizer`` before being interpolated.
"""

from __future__ import annotations

from typing import Final

# Each snippet uses ``%(arg)s``-style placeholders. Arguments are validated
# and HTML/JS-escaped before substitution by the tool layer.
ALLOWED_SNIPPETS: Final[dict[str, str]] = {
    "scroll_to_bottom": "window.scrollTo(0, document.body.scrollHeight);",
    "scroll_to_top": "window.scrollTo(0, 0);",
    "scroll_by": "window.scrollBy(0, %(pixels)s);",
    "get_visible_text": (
        "(() => document.body.innerText.substring(0, %(max_chars)s))();"
    ),
    "query_selector_text": (
        "(() => { const el = document.querySelector(%(selector)s);"
        " return el ? el.innerText : null; })();"
    ),
    "count_selector": (
        "(() => document.querySelectorAll(%(selector)s).length)();"
    ),
    "get_links": (
        "(() => Array.from(document.querySelectorAll('a'))"
        ".slice(0, %(max_links)s)"
        ".map(a => ({href: a.href, text: a.innerText.substring(0, 200)})))();"
    ),
}


__all__ = ["ALLOWED_SNIPPETS"]
