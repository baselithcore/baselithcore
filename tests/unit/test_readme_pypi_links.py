"""README.md is the PyPI long description: every link must be absolute.

PyPI renders the README with no repository behind it, so a relative link
(``LICENSE``, ``media/logo.png``) is a 404 or a broken image there, while it
looks fine on GitHub — which is how they went unnoticed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

README = Path(__file__).resolve().parents[2] / "README.md"
_MARKDOWN_TARGET = re.compile(r"\]\(([^)\s]+)")
_HTML_TARGET = re.compile(r'\b(?:src|srcset|href)="([^"]+)"')
_ABSOLUTE = re.compile(r"^(?:https://|mailto:|#)")


def test_readme_links_and_images_are_absolute() -> None:
    text = README.read_text(encoding="utf-8")
    targets = _MARKDOWN_TARGET.findall(text) + _HTML_TARGET.findall(text)

    relative = sorted({t for t in targets if not _ABSOLUTE.match(t)})

    assert targets, "no links found — the pattern stopped matching"
    assert relative == [], f"relative links break on PyPI: {relative}"
