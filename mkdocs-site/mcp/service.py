import re
import tomllib as toml_loader
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.observability.logging import get_logger

logger = get_logger(__name__)

# Words of five characters or more carry the topical signal. Shorter tokens are
# shared by every page in the corpus and only flatten the comparison.
_KEYWORD_RE = re.compile(r"\w{5,}")

# Jaccard overlap above which two pages count as related. The previous rule —
# "at least three shared long words" — matched essentially every pair of pages
# in a 115-page corpus, so every document was related to every other one.
_RELATED_MIN_SIMILARITY = 0.12
_RELATED_LIMIT = 5
_SEARCH_LIMIT = 10


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

    def __post_init__(self) -> None:
        self.content_lower = self.content.lower()
        self.title_lower = self.title.lower()
        # Precomputed once per read. find_related_pages used to re-tokenize the
        # whole corpus — over two megabytes — on every single call.
        self.keywords = frozenset(_KEYWORD_RE.findall(self.content_lower))


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

    async def initialize(self):
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

    def _load(self, page_path: str, title: str | None = None, indexed: bool = False):
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

    def _refresh(self, page_path: str):
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

    async def get_page_content(self, page_path: str) -> str | None:
        """Read the content of a markdown page with freshness-checked caching."""
        doc = self._refresh(page_path)
        return doc.content if doc else None

    async def get_page_by_title(self, title_query: str) -> dict[str, str] | None:
        """Find a page by exact or partial title match."""
        title_query = title_query.lower()
        for page in self._pages:
            if (
                title_query == page["title"].lower()
                or title_query in page["title"].lower()
            ):
                content = await self.get_page_content(page["path"])
                return {
                    "title": page["title"],
                    "path": page["path"],
                    "content": content or "No content available.",
                }
        return None

    def get_nav_tree(self) -> list[Any]:
        """Return the hierarchical navigation structure."""
        return self._nav_tree

    def get_all_pages(self) -> list[dict[str, str]]:
        """Return a list of all indexed pages."""
        return self._pages

    async def get_docs_batch(self, paths: list[str]) -> dict[str, str]:
        """Fetch multiple documentation pages in a single call."""
        results = {}
        for path in paths:
            content = await self.get_page_content(path)
            if content:
                results[path] = content
        return results

    def get_docs_summary(self) -> list[dict[str, str]]:
        """Return a list of all pages with titles, paths, and short summaries."""
        summaries = []
        for doc in self._refresh_all():
            snippet = doc.content[:200].strip().replace("\n", " ")
            if len(doc.content) > 200:
                snippet += "..."
            summaries.append({"title": doc.title, "path": doc.path, "summary": snippet})
        return summaries

    # -------------------------------------------------------------------------
    # Search
    # -------------------------------------------------------------------------

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
                # Find a better snippet: first occurrence of the query
                idx = doc.content_lower.find(query_lower)
                if idx == -1:
                    idx = 0

                # Contextual snippet
                start = max(0, idx - 60)
                end = min(len(doc.content), idx + 140)
                snippet = doc.content[start:end].strip()
                if start > 0:
                    snippet = f"...{snippet}"
                if end < len(doc.content):
                    snippet = f"{snippet}..."

                results.append(
                    {
                        "title": doc.title,
                        "path": doc.path,
                        "snippet": snippet.replace("\n", " "),
                        "score": score,
                    }
                )

        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    async def search(self, query: str) -> list[dict[str, Any]]:
        """Keyword search across the documentation with relevance scoring."""
        return self._rank(query, self._refresh_all())[:_SEARCH_LIMIT]

    async def search_in_section(
        self, query: str, section_name: str
    ) -> list[dict[str, Any]]:
        """Perform a keyword search restricted to a specific documentation section.

        The section filter is applied to the corpus *before* ranking. Filtering
        the global top ten instead — as this used to — returned nothing at all
        whenever the section's best hit was not also a top-ten hit site-wide.
        """
        section_lower = section_name.lower()
        scoped = [
            doc for doc in self._refresh_all() if section_lower in doc.title_lower
        ]
        return self._rank(query, scoped)[:_SEARCH_LIMIT]

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
                        "shared_keywords": len(common),
                    }
                )

        related.sort(key=lambda x: x["score"], reverse=True)
        return related[:_RELATED_LIMIT]
