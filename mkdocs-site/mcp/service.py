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
        logger.info(f"DocsService initialized with {len(self._pages)} pages")

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
        """Read the content of a markdown page."""
        # Handle cases where path might be relative to docs_dir
        full_path = self.docs_dir / page_path
        if not full_path.exists():
            return None

        with open(full_path, "r", encoding="utf-8") as f:
            return f.read()

    async def search(self, query: str) -> List[Dict[str, Any]]:
        """Keyword search across documentation with relevance scoring."""
        results = []
        query_lower = query.lower()

        for page in self._pages:
            content = await self.get_page_content(page["path"])
            if not content:
                continue

            content_lower = content.lower()
            title_lower = page["title"].lower()

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
