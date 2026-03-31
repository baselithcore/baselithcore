try:
    import tomllib
except ImportError:
    # For Python < 3.11
    import toml as tomllib
from pathlib import Path
from typing import List, Dict, Any, Optional
from core.observability.logging import get_logger

logger = get_logger(__name__)


class DocsService:
    """Service to handle Docs site (Zensical) parsing and searching."""

    def __init__(self, root_dir: str):
        self.root_dir = Path(root_dir)
        self.config_path = self.root_dir / "zensical.toml"
        self.docs_dir = self.root_dir / "docs"
        self._config: Dict[str, Any] = {}
        self._pages: List[Dict[str, str]] = []
        self._nav_tree: List[Any] = []
        self._content_cache: Dict[str, str] = {}
        self._search_index: List[Dict[str, Any]] = []

    async def initialize(self):
        """Load Zensical configuration and index pages."""
        if not self.config_path.exists():
            logger.error(f"Zensical config not found at {self.config_path}")
            return

        with open(self.config_path, "rb") as f:
            self._config = tomllib.load(f)

        project_config = self._config.get("project", {})
        self._nav_tree = project_config.get("nav", [])
        self._pages = self._parse_nav(self._nav_tree)

        # Pre-load content for all pages
        self._content_cache = {}
        self._search_index = []

        for page in self._pages:
            path = page["path"]
            full_path = self.docs_dir / path
            if full_path.exists():
                try:
                    with open(full_path, "r", encoding="utf-8") as f:
                        content = f.read()
                        self._content_cache[path] = content
                        self._search_index.append(
                            {
                                "page": page,
                                "content": content,
                                "content_lower": content.lower(),
                                "title_lower": page["title"].lower(),
                            }
                        )
                except Exception as e:
                    logger.error(f"Failed to read doc page {path}: {e}")

        logger.info(
            f"DocsService initialized with {len(self._pages)} pages (indexed {len(self._search_index)})"
        )

    def _parse_nav(self, nav: List[Any], prefix: str = "") -> List[Dict[str, str]]:
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

    async def get_page_content(self, page_path: str) -> Optional[str]:
        """Read the content of a markdown page with memory caching."""
        # Check cache first
        if page_path in self._content_cache:
            return self._content_cache[page_path]

        # Handle cases where path might be relative to docs_dir
        full_path = self.docs_dir / page_path
        if not full_path.exists():
            return None

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()
                self._content_cache[page_path] = content
                return content
        except Exception as e:
            logger.warning(f"Error reading page {page_path}: {e}")
            return None

    async def search(self, query: str) -> List[Dict[str, Any]]:
        """Keyword search across pre-indexed documentation with relevance scoring."""
        results = []
        query_lower = query.lower()

        # Iterate over pre-indexed content
        for entry in self._search_index:
            content_lower = entry["content_lower"]
            title_lower = entry["title_lower"]
            page = entry["page"]
            content = entry["content"]

            score = 0
            if query_lower in title_lower:
                score += 100

            occurrences = content_lower.count(query_lower)
            if occurrences > 0:
                score += min(50, occurrences * 5)

            if score > 0:
                # Find a better snippet: first occurrence of the query
                idx = content_lower.find(query_lower)
                if idx == -1:
                    idx = 0

                # Contextual snippet
                start = max(0, idx - 60)
                end = min(len(content), idx + 140)
                snippet = content[start:end].strip()
                if start > 0:
                    snippet = f"...{snippet}"
                if end < len(content):
                    snippet = f"{snippet}..."

                results.append(
                    {
                        "title": page["title"],
                        "path": page["path"],
                        "snippet": snippet.replace("\n", " "),
                        "score": score,
                    }
                )

        # Sort by score descending
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:10]

    def get_nav_tree(self) -> List[Any]:
        """Return the hierarchical navigation structure."""
        return self._nav_tree

    async def get_page_by_title(self, title_query: str) -> Optional[Dict[str, str]]:
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

    def get_all_pages(self) -> List[Dict[str, str]]:
        """Return a list of all indexed pages."""
        return self._pages

    async def get_docs_batch(self, paths: List[str]) -> Dict[str, str]:
        """Fetch multiple documentation pages in a single call."""
        results = {}
        for path in paths:
            content = await self.get_page_content(path)
            if content:
                results[path] = content
        return results

    def get_docs_summary(self) -> List[Dict[str, str]]:
        """Return a list of all pages with titles, paths, and short summaries."""
        summaries = []
        for entry in self._search_index:
            content = entry["content"]
            # Extract first 200 chars as summary
            snippet = content[:200].strip().replace("\n", " ")
            if len(content) > 200:
                snippet += "..."
            summaries.append(
                {
                    "title": entry["page"]["title"],
                    "path": entry["page"]["path"],
                    "summary": snippet,
                }
            )
        return summaries

    def find_related_pages(self, path: str) -> List[Dict[str, Any]]:
        """Find pages related to the target path using keyword overlap analysis."""
        import re

        target_content = self._content_cache.get(path)
        if not target_content:
            return []

        # Simple keyword extractor for longer significant words
        def get_keywords(text: str) -> set[str]:
            return set(re.findall(r"\w{5,}", text.lower()))

        target_keywords = get_keywords(target_content)
        related = []

        for entry in self._search_index:
            p_path = entry["page"]["path"]
            if p_path == path:
                continue

            # Compare keywords
            p_keywords = get_keywords(entry["content"])
            common = target_keywords.intersection(p_keywords)
            if len(common) >= 3:  # Threshold for relevance
                related.append(
                    {
                        "title": entry["page"]["title"],
                        "path": p_path,
                        "score": len(common),
                    }
                )

        related.sort(key=lambda x: x["score"], reverse=True)
        return related[:5]

    async def search_in_section(
        self, query: str, section_name: str
    ) -> List[Dict[str, Any]]:
        """Perform a keyword search restricted to a specific documentation section."""
        all_results = await self.search(query)
        section_lower = section_name.lower()
        return [r for r in all_results if section_lower in r["title"].lower()]
