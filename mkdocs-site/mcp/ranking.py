"""Section-level BM25 ranking for the documentation search.

The search used to count occurrences of the *whole query string* in each page.
An agent asks in words, not in exact phrases: "how do I create a plugin" or
"plugin manifest integrity" matched nothing at all, because no page contains
that literal sentence. This module ranks the same corpus the way a search
engine would:

* the unit is a section (``##``/``###`` block, plus each page's lead), which is
  also the unit ``get_doc_section`` serves, so a hit is cheap to read in full;
* every query term counts on its own (BM25), weighted by how rare it is, and a
  section matching more of the terms beats one repeating a single term;
* the section heading and the page title weigh more than body text, and an
  exact phrase match earns a bonus;
* code identifiers survive tokenizing whole *and* as parts, so both
  ``register_token_sink`` and ``token sink`` find the same section.

No dependency beyond the standard library: the docs server stays runnable from
a bare checkout.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from .sections import Section, preamble_end

# Words, with identifiers (``baselith.core.mcp``, ``multi-tenancy``,
# ``LLM_STREAM_AGENT_TEXT``) kept as one token; ``terms`` adds their parts.
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[_.\-/][a-z0-9]+)*")
_PART_RE = re.compile(r"[_.\-/]")
_H1_RE = re.compile(r"^#\s.*$", re.MULTILINE)

# Function words carry no topic. Agents phrase queries as questions, and a few
# Italian ones are listed because the assistant that asks may not translate.
_STOPWORDS = frozenset(
    """
    a an the and or but if of to in on at by for from with without into onto as
    is are was were be been being do does did done have has had can could should
    would will shall may might must not no yes this that these those it its i me
    my we our you your they their there here what which who whom whose when
    where why how all any each some such than then so too very just also about
    over under up down out via per using use used get set
    il lo la le gli un una uno di da del della dei delle che come per con su
    non e o ma se nel nella sono essere fare
    """.split()
)

# BM25 parameters: the usual defaults, which suit short technical sections.
_K1 = 1.2
_B = 0.75

# Extra term frequency granted to the words of a section heading (on top of the
# heading line already being part of the body) and of the page breadcrumb.
_HEADING_BOOST = 2
_TITLE_BOOST = 1

# Share of the score that depends on how many distinct query terms a section
# matched: 0.3 floor, so a strong single-term hit still ranks.
_COVERAGE_FLOOR = 0.3
_PHRASE_BONUS = 3.0

# Reciprocal Rank Fusion constant: the usual 60, which keeps one list's top
# hit from drowning a section both lists rank well.
_RRF_K = 60

# The home page is a grid of cards pointing at the real pages: a match there
# is rarely the answer, so it counts half.
_DEMOTED_PAGES = frozenset({"index.md"})
_DEMOTION = 0.5

# Two sections per page at most: five hits from one long page answer one
# question five times instead of pointing at the other pages.
_PER_PAGE = 2


class Page(Protocol):
    """What the index needs from a cached documentation page."""

    path: str
    title: str
    content: str
    sections: list[Section]


def normalize(text: str) -> str:
    """Lowercase and strip accents, so ``Modalità`` and ``modalita`` agree."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def stem(token: str) -> str:
    """Fold plurals onto the singular: ``plugins``/``plugin``, ``policies``/``policy``.

    Deliberately crude and applied to both sides, so it only has to be
    consistent, not linguistically right.
    """
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if (
        len(token) > 3
        and token.endswith("s")
        and not token.endswith(("ss", "us", "is"))
    ):
        return token[:-1]
    return token


def terms(text: str) -> list[str]:
    """The index terms of ``text``, in order, repeats kept."""
    out: list[str] = []
    for raw in _TOKEN_RE.findall(normalize(text)):
        parts = _PART_RE.split(raw)
        for token in (raw, *parts) if len(parts) > 1 else (raw,):
            if len(token) > 1 and token not in _STOPWORDS:
                out.append(stem(token))
    return out


@dataclass(eq=False, slots=True)
class Unit:
    """One searchable span of a page: its lead, or one flat section."""

    path: str
    #: The page's breadcrumb title (``Core Modules > Memory``).
    title: str
    section: Section | None
    start: int
    end: int
    text: str
    tf: Counter[str] = field(repr=False)
    length: int = 0


@dataclass(frozen=True, slots=True)
class Match:
    """A ranked unit, with where in the page its snippet should start."""

    unit: Unit
    score: float
    focus: int


class Index:
    """BM25 over every section of the given pages, built once per corpus revision."""

    def __init__(self, pages: Sequence[Page]) -> None:
        self.units: list[Unit] = []
        df: Counter[str] = Counter()
        for page in pages:
            for unit in _units(page):
                self.units.append(unit)
                df.update(unit.tf.keys())
        total = len(self.units)
        self._avg = sum(u.length for u in self.units) / total if total else 1.0
        self._idf = {
            term: math.log(1 + (total - n + 0.5) / (n + 0.5)) for term, n in df.items()
        }

    def rank(
        self, query: str, allowed: Callable[[str], bool] | None = None
    ) -> list[Match]:
        """Every section matching ``query``, best first."""
        wanted = list(dict.fromkeys(terms(query)))
        if not wanted:
            return []
        phrase = " ".join(normalize(query).split()) if len(wanted) > 1 else ""

        scored: list[Match] = []
        for unit in self.units:
            if allowed is not None and not allowed(unit.path):
                continue
            match = self._score(unit, wanted, phrase)
            if match is not None:
                scored.append(match)
        scored.sort(key=lambda m: m.score, reverse=True)
        return scored

    def search(
        self,
        query: str,
        limit: int,
        allowed: Callable[[str], bool] | None = None,
    ) -> list[Match]:
        """The best sections for ``query`` by keywords alone, at most two per page."""
        return diversify(self.rank(query, allowed), limit)

    def _score(self, unit: Unit, wanted: list[str], phrase: str) -> Match | None:
        score = 0.0
        matched: list[str] = []
        norm = 1 - _B + _B * unit.length / self._avg
        for term in wanted:
            freq = unit.tf.get(term, 0)
            if not freq:
                continue
            matched.append(term)
            score += self._idf[term] * freq * (_K1 + 1) / (freq + _K1 * norm)
        if not matched:
            return None
        score *= _COVERAGE_FLOOR + (1 - _COVERAGE_FLOOR) * len(matched) / len(wanted)
        focus = -1
        if phrase:
            focus = unit.text.find(phrase)
            if focus >= 0:
                score += _PHRASE_BONUS
        if focus < 0:
            # Anchor the snippet on the rarest term the section contains.
            rarest = max(matched, key=lambda t: self._idf[t])
            focus = unit.text.find(rarest)
        start = unit.start + max(focus, 0)
        return Match(unit, round(score, 3), start)


def diversify(matches: Sequence[Match], limit: int) -> list[Match]:
    """The first ``limit`` matches, at most two from any one page."""
    picked: list[Match] = []
    per_page: Counter[str] = Counter()
    for match in matches:
        if per_page[match.unit.path] >= _PER_PAGE:
            continue
        per_page[match.unit.path] += 1
        picked.append(match)
        if len(picked) >= limit:
            break
    return picked


def fuse(lexical: Sequence[Match], semantic: Sequence[Unit]) -> list[Match]:
    """Merge the keyword and the semantic rankings by Reciprocal Rank Fusion.

    Ranks, not scores, are combined: BM25 scores and cosine similarities live on
    scales that cannot be added. A section ranked high by both lists beats one
    ranked high by either; a section only the semantic list found (a question
    in another language, a synonym) still makes it in. Hits on the site's home
    page — feature cards linking elsewhere — are demoted.
    """
    fused: dict[int, float] = {}
    best: dict[int, Match] = {}
    for rank, match in enumerate(lexical):
        key = id(match.unit)
        fused[key] = fused.get(key, 0.0) + 1 / (_RRF_K + rank + 1)
        best[key] = match
    for rank, unit in enumerate(semantic):
        key = id(unit)
        fused[key] = fused.get(key, 0.0) + 1 / (_RRF_K + rank + 1)
        best.setdefault(key, Match(unit, 0.0, unit.start))
    out = []
    for key, score in fused.items():
        match = best[key]
        if match.unit.path in _DEMOTED_PAGES:
            score *= _DEMOTION
        out.append(Match(match.unit, round(score * 1000, 3), match.focus))
    out.sort(key=lambda m: m.score, reverse=True)
    return out


def _units(page: Page) -> list[Unit]:
    """The lead of ``page`` (when it has text) and each of its flat sections."""
    title_terms = terms(page.title)
    lead_end = preamble_end(page.sections) if page.sections else len(page.content)
    spans: list[tuple[Section | None, int, int]] = [(None, 0, lead_end)]
    spans.extend((s, s.start, s.end) for s in page.sections)

    units: list[Unit] = []
    for section, start, end in spans:
        raw = page.content[start:end]
        body = terms(raw)
        # A lead that is only the page's H1 says nothing its sections do not.
        if not body or (section is None and not terms(_H1_RE.sub("", raw))):
            continue
        tf = Counter(body)
        if section is not None:
            for term in terms(section.title):
                tf[term] += _HEADING_BOOST
        for term in title_terms:
            tf[term] += _TITLE_BOOST
        units.append(
            Unit(
                page.path,
                page.title,
                section,
                start,
                end,
                normalize(raw),
                tf,
                sum(tf.values()),
            )
        )
    return units


__all__ = [
    "Index",
    "Match",
    "Page",
    "Unit",
    "diversify",
    "fuse",
    "normalize",
    "stem",
    "terms",
]
