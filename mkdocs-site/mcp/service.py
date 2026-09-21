import re
import tomllib as toml_loader
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.observability.logging import get_logger

from .sections import (
    Section,
    enclosing_section,
    estimate_tokens,
    find_section,
    parse_sections,
    preamble_end,
    span_with_children,
)

logger = get_logger(__name__)

# Words of five characters or more carry the topical signal. Shorter tokens are
# shared by every page in the corpus and only flatten the comparison.
_KEYWORD_RE = re.compile(r"\w{5,}")

# Jaccard overlap above which two pages count as related. The previous rule —
# "at least three shared long words" — matched essentially every pair of pages
# in a 115-page corpus, so every document was related to every other one.
_RELATED_MIN_SIMILARITY = 0.12
_RELATED_LIMIT = 5

# Default number of search hits. Five named sections answer a question; ten
# full-page hits mostly pad the context window.
_SEARCH_LIMIT = 5
_MAX_SEARCH_LIMIT = 20

# A page above this estimated size is answered with its outline instead of its
# body, so a single lookup cannot spend a fifth of an agent's context. The
# caller can still ask for the whole thing explicitly.
_PAGE_TOKEN_BUDGET = 6000

_SNIPPET_BEFORE = 60
_SNIPPET_AFTER = 140


@dataclass
class _Doc:
    """One documentation page held in memory, with the fields search needs."""

    path: str
    title: str
    content: str
    mtime_ns: int
    # Indexed pages are the ones declared in the site nav: only those are
    # searched. A page fetched by explicit path is cached but not indexed.
    indexed: bool = False
    content_lower: str = field(default="", repr=False)
    title_lower: str = field(default="", repr=False)
    keywords: frozenset[str] = field(default_factory=frozenset, repr=False)
    sections: list[Section] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self.content_lower = self.content.lower()
        self.title_lower = self.title.lower()
        # Precomputed once per read. find_related_pages used to re-tokenize the
        # whole corpus — over two megabytes — on every single call.
        self.keywords = frozenset(_KEYWORD_RE.findall(self.content_lower))
        self.sections = parse_sections(self.content)

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.content)

    def outline(self) -> list[dict[str, Any]]:
        return [
            {
                "heading": s.title,
                "anchor": s.anchor,
                "level": s.level,
                "tokens": s.tokens(self.content),
            }
            for s in self.sections
        ]


class DocsService:
    """Service to handle Docs site (Zensical) parsing and searching."""

    def __init__(self, root_dir: str):
        self.root_dir = Path(root_dir)
        self.config_path = self.root_dir / "zensical.toml"
        self.docs_dir = self.root_dir / "docs"
        # Resolved once: every path handed in by a caller is checked against it.
        self._docs_root = self.docs_dir.resolve()
        self._config: dict[str, Any] = {}
        self._pages: list[dict[str, str]] = []
        self._nav_tree: list[Any] = []
        self._docs: dict[str, _Doc] = {}
        # Bumped whenever a page is re-read, added or dropped, so callers that
        # cache derived artefacts can tell when theirs went stale.
        self._revision = 0

    @property
    def revision(self) -> int:
        """Counter that changes whenever the indexed content changes."""
        return self._revision

    async def initialize(self) -> None:
        """Load Zensical configuration and index pages."""
        if not self.config_path.exists():
            logger.error(f"Zensical config not found at {self.config_path}")
            return

        with open(self.config_path, "rb") as f:
            self._config = toml_loader.load(f)

        project_config = self._config.get("project", {})
        self._nav_tree = project_config.get("nav", [])
        self._pages = self._parse_nav(self._nav_tree)

        self._docs = {}
        for page in self._pages:
            self._load(page["path"], title=page["title"], indexed=True)

        indexed = sum(1 for doc in self._docs.values() if doc.indexed)
        logger.info(
            f"DocsService initialized with {len(self._pages)} pages (indexed {indexed})"
        )

    def _parse_nav(self, nav: list[Any], prefix: str = "") -> list[dict[str, str]]:
        """Recursively parses the nav structure from zensical.toml."""
        pages = []
        for item in nav:
            if isinstance(item, str):
                pages.append({"title": item.replace(".md", ""), "path": item})
            elif isinstance(item, dict):
                for title, value in item.items():
                    current_title = f"{prefix} > {title}" if prefix else title
                    if isinstance(value, str):
                        pages.append({"title": current_title, "path": value})
                    elif isinstance(value, list):
                        pages.extend(self._parse_nav(value, current_title))
        return pages

    # -------------------------------------------------------------------------
    # Path handling and cache freshness
    # -------------------------------------------------------------------------

    def _resolve(self, page_path: str) -> Path | None:
        """Resolve a caller-supplied path, refusing anything outside the docs.

        The path arrives over MCP from a model, so it is untrusted input:
        without this check ``../../.env`` reads whatever the server process can.
        """
        candidate = (self.docs_dir / page_path).resolve()
        if candidate != self._docs_root and self._docs_root not in candidate.parents:
            logger.warning(f"Rejected out-of-tree doc path: {page_path}")
            return None
        return candidate

    def _load(
        self, page_path: str, title: str | None = None, indexed: bool = False
    ) -> _Doc | None:
        """Read a page from disk into the cache, returning the entry or None."""
        full_path = self._resolve(page_path)
        if full_path is None or not full_path.is_file():
            return None
        try:
            stat = full_path.stat()
            content = full_path.read_text(encoding="utf-8")
        except OSError as e:
            logger.error(f"Failed to read doc page {page_path}: {e}")
            return None

        existing = self._docs.get(page_path)
        doc = _Doc(
            path=page_path,
            title=title or (existing.title if existing else page_path),
            content=content,
            mtime_ns=stat.st_mtime_ns,
            indexed=indexed or bool(existing and existing.indexed),
        )
        self._docs[page_path] = doc
        self._revision += 1
        return doc

    def _refresh(self, page_path: str) -> _Doc | None:
        """Return the cached page, re-reading it if the file changed on disk.

        The docs are edited while the server runs — that is the whole point of
        pointing an agent at them — so a cache populated once at startup starts
        answering with yesterday's text within minutes.
        """
        doc = self._docs.get(page_path)
        if doc is None:
            return self._load(page_path)

        full_path = self._resolve(page_path)
        if full_path is None:
            return doc
        try:
            mtime_ns = full_path.stat().st_mtime_ns
        except OSError:
            # Page vanished (renamed, deleted): drop it rather than serve it.
            self._docs.pop(page_path, None)
            self._revision += 1
            return None

        if mtime_ns != doc.mtime_ns:
            return self._load(page_path, title=doc.title, indexed=doc.indexed)
        return doc

    def _refresh_all(self) -> list[_Doc]:
        """Refresh every indexed page and return the current index.

        One ``stat`` per page — cheap next to re-reading the corpus, and it is
        what keeps search results consistent with the files on disk.
        """
        for path in list(self._docs):
            self._refresh(path)
        return [doc for doc in self._docs.values() if doc.indexed]

    # -------------------------------------------------------------------------
    # Reads
    # -------------------------------------------------------------------------

    def get_nav_tree(self) -> list[Any]:
        """Return the hierarchical navigation structure."""
        return self._nav_tree

    def get_all_pages(self) -> list[dict[str, str]]:
        """Return a list of all indexed pages."""
        return self._pages

    def list_pages(self, section: str | None = None) -> list[dict[str, str]]:
        """List pages, optionally only those under one navigation section."""
        if not section:
            return self._pages
        needle = section.lower()
        return [p for p in self._pages if needle in p["title"].lower()]

    async def get_page_content(self, page_path: str) -> str | None:
        """Read the raw markdown of a page, with freshness-checked caching."""
        doc = self._refresh(page_path)
        return doc.content if doc else None

    def get_outline(self, page_path: str) -> dict[str, Any] | None:
        """Return a page's headings and their sizes, without its body."""
        doc = self._refresh(page_path)
        if doc is None:
            return None
        return {
            "path": doc.path,
            "title": doc.title,
            "tokens": doc.tokens,
            "lead_tokens": estimate_tokens(doc.content[: preamble_end(doc.sections)]),
            "sections": doc.outline(),
        }

    def get_section(self, page_path: str, heading: str) -> dict[str, Any] | None:
        """Return one section of a page, with its nested subsections."""
        doc = self._refresh(page_path)
        if doc is None:
            return None
        section = find_section(doc.sections, heading)
        if section is None:
            return None

        start, end = span_with_children(doc.sections, section)
        body = doc.content[start:end].strip()
        return {
            "path": doc.path,
            "page_title": doc.title,
            "heading": section.title,
            "anchor": section.anchor,
            "level": section.level,
            "tokens": estimate_tokens(body),
            "content": body,
        }

    def get_page(self, page_path: str, full: bool = False) -> dict[str, Any] | None:
        """Return a page's body, or its outline when the body blows the budget.

        A handful of pages in this site cost over twenty thousand tokens.
        Returning the outline instead turns one ruinous call into two cheap
        ones, and ``full=True`` is there for the times the whole page is
        genuinely what is wanted.
        """
        doc = self._refresh(page_path)
        if doc is None:
            return None

        if full or doc.tokens <= _PAGE_TOKEN_BUDGET or not doc.sections:
            return {
                "path": doc.path,
                "title": doc.title,
                "tokens": doc.tokens,
                "content": doc.content,
            }

        return {
            "path": doc.path,
            "title": doc.title,
            "tokens": doc.tokens,
            "truncated": True,
            "reason": (
                f"Page is ~{doc.tokens} tokens, over the {_PAGE_TOKEN_BUDGET} "
                "budget. Request one heading with get_doc_section, or call "
                "again with full=true."
            ),
            "sections": doc.outline(),
        }

    async def get_docs_batch(
        self, paths: list[str], full: bool = False
    ) -> dict[str, Any]:
        """Fetch several pages in one round trip, under the same size budget."""
        results: dict[str, Any] = {}
        for path in paths:
            page = self.get_page(path, full=full)
            if page is not None:
                results[path] = page
        return results

    # -------------------------------------------------------------------------
    # Search
    # -------------------------------------------------------------------------

    def _hit(self, doc: _Doc, query_lower: str, score: int) -> dict[str, Any]:
        """Build one search result, pinned to the section the match sits in."""
        idx = doc.content_lower.find(query_lower)
        if idx == -1:
            idx = 0

        start = max(0, idx - _SNIPPET_BEFORE)
        end = min(len(doc.content), idx + _SNIPPET_AFTER)
        snippet = doc.content[start:end].strip()
        if start > 0:
            snippet = f"...{snippet}"
        if end < len(doc.content):
            snippet = f"{snippet}..."

        hit: dict[str, Any] = {
            "title": doc.title,
            "path": doc.path,
            "snippet": snippet.replace("\n", " "),
            "score": score,
        }
        section = enclosing_section(doc.sections, idx)
        if section is not None:
            # The heading is the whole point: it is what get_doc_section takes,
            # so a hit can be read in full without pulling the page.
            hit["heading"] = section.title
            hit["anchor"] = section.anchor
            hit["section_tokens"] = section.tokens(doc.content)
        return hit

    def _rank(self, query: str, docs: list[_Doc]) -> list[dict[str, Any]]:
        """Score the given pages against the query, best first, untruncated."""
        query_lower = query.lower()
        results = []

        for doc in docs:
            score = 0
            if query_lower in doc.title_lower:
                score += 100

            occurrences = doc.content_lower.count(query_lower)
            if occurrences > 0:
                score += min(50, occurrences * 5)

            if score > 0:
                results.append(self._hit(doc, query_lower, score))

        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    async def search(
        self,
        query: str,
        section: str | None = None,
        limit: int = _SEARCH_LIMIT,
    ) -> list[dict[str, Any]]:
        """Keyword search, optionally scoped to one navigation section.

        The scope is applied to the corpus *before* ranking. Filtering the
        global top hits instead — as this used to — returned nothing at all
        whenever a section's best hit was not also a top hit site-wide.
        """
        docs = self._refresh_all()
        if section:
            needle = section.lower()
            docs = [doc for doc in docs if needle in doc.title_lower]
        limit = max(1, min(limit, _MAX_SEARCH_LIMIT))
        return self._rank(query, docs)[:limit]

    def find_related_pages(self, path: str) -> list[dict[str, Any]]:
        """Find pages related to the target path using keyword overlap analysis."""
        target = self._refresh(path)
        if target is None or not target.keywords:
            return []

        related = []
        for doc in self._refresh_all():
            if doc.path == path or not doc.keywords:
                continue

            common = target.keywords & doc.keywords
            union = len(target.keywords | doc.keywords)
            similarity = len(common) / union if union else 0.0
            if similarity >= _RELATED_MIN_SIMILARITY:
                related.append(
                    {
                        "title": doc.title,
                        "path": doc.path,
                        "score": round(similarity, 4),
                    }
                )

        related.sort(key=lambda x: x["score"], reverse=True)
        return related[:_RELATED_LIMIT]
