"""Heading-aware slicing of a markdown page.

A documentation page is the wrong unit to hand an agent: the median page in
this site is a few thousand tokens and the largest is well over twenty
thousand, while the answer to a question is usually one section. This module
turns a page into its headings so the rest of the server can serve, and cite,
that section instead of the whole file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Tokens are estimated, not counted: the server has no tokenizer for the
# client's model and does not need one. Four characters per token is the usual
# rule of thumb for English prose and holds well enough on markdown to size a
# budget.
_CHARS_PER_TOKEN = 4

# Only H2 and H3 open a section. H1 is the page title, and H4 and below are
# too fine to be worth a separate round trip.
_MIN_LEVEL = 2
_MAX_LEVEL = 3

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
# Inline markup stripped before slugifying, so `## The `LoopBudget` contract`
# anchors the way the rendered page does.
_INLINE_RE = re.compile(r"[`*_]+")
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_NON_SLUG_RE = re.compile(r"[^\w\- ]+")


def estimate_tokens(text: str) -> int:
    """Approximate the token cost of a string."""
    return round(len(text) / _CHARS_PER_TOKEN)


def slugify(heading: str) -> str:
    """Render a heading the way the docs site anchors it."""
    text = _LINK_RE.sub(r"\1", heading)
    text = _INLINE_RE.sub("", text)
    text = _NON_SLUG_RE.sub("", text.lower()).strip()
    return re.sub(r"[\s-]+", "-", text)


@dataclass(frozen=True)
class Section:
    """One ``##``/``###`` block of a page, located by byte offsets."""

    title: str
    anchor: str
    level: int
    start: int
    end: int

    def body(self, content: str) -> str:
        return content[self.start : self.end].strip()

    def tokens(self, content: str) -> int:
        return estimate_tokens(content[self.start : self.end])


def parse_sections(content: str) -> list[Section]:
    """Split a page into its H2/H3 sections, in document order.

    Headings inside fenced code blocks are ignored — shell comments and
    Python comments would otherwise cut a page into nonsense.
    """
    sections: list[Section] = []
    offset = 0
    in_fence = False
    fence_marker = ""
    pending: list[tuple[str, int, int]] = []  # (title, level, heading_start)

    for line in content.splitlines(keepends=True):
        fence = _FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif marker == fence_marker:
                in_fence, fence_marker = False, ""
            offset += len(line)
            continue

        # Column zero only: an indented ``#`` is a code block or a list item.
        if not in_fence and line.startswith("#"):
            match = _HEADING_RE.match(line)
            if match and _MIN_LEVEL <= len(match.group(1)) <= _MAX_LEVEL:
                if pending:
                    title, level, start = pending.pop()
                    sections.append(
                        Section(title, slugify(title), level, start, offset)
                    )
                pending.append((match.group(2).strip(), len(match.group(1)), offset))

        offset += len(line)

    if pending:
        title, level, start = pending.pop()
        sections.append(Section(title, slugify(title), level, start, len(content)))

    return sections


def span_with_children(sections: list[Section], section: Section) -> tuple[int, int]:
    """The offsets of a section *including* its nested subsections.

    Sections are sliced flat, so an ``##`` block stops where its first ``###``
    begins. Asking for the ``##`` almost always means the whole topic, so the
    span runs on until the next heading at the same level or above.
    """
    start, end = section.start, section.end
    seen = False
    for candidate in sections:
        if candidate is section:
            seen = True
            continue
        if not seen:
            continue
        if candidate.level <= section.level:
            return start, candidate.start
        end = candidate.end
    return start, end


def preamble_end(sections: list[Section]) -> int:
    """Offset where the first section starts — everything before it is the lead."""
    return sections[0].start if sections else 0


def find_section(sections: list[Section], wanted: str) -> Section | None:
    """Resolve a heading the way a caller is likely to have written it.

    Tried in order: the anchor, the exact title, then a substring of a title.
    A model that read a search result will pass the anchor; a person typing
    from memory will pass something close to the title.
    """
    needle = wanted.strip().lower()
    if not needle:
        return None

    slug = slugify(wanted)
    for section in sections:
        if section.anchor == slug:
            return section
    for section in sections:
        if section.title.lower() == needle:
            return section
    for section in sections:
        if needle in section.title.lower():
            return section
    return None


def enclosing_section(sections: list[Section], offset: int) -> Section | None:
    """The section a given position in the page falls inside, if any."""
    found = None
    for section in sections:
        if section.start <= offset:
            found = section
        else:
            break
    return found
