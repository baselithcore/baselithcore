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

    async def initialize(self):
        """Load Zensical configuration and index pages."""
        if not self.config_path.exists():
            logger.error(f"Zensical config not found at {self.config_path}")
            return

        with open(self.config_path, "rb") as f:
            self._config = tomllib.load(f)

        project_config = self._config.get("project", {})
        self._pages = self._parse_nav(project_config.get("nav", []))
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
        """Simple keyword search across documentation."""
        results = []
        query = query.lower()

        for page in self._pages:
            content = await self.get_page_content(page["path"])
            if not content:
                continue

            if query in content.lower() or query in page["title"].lower():
                # Find a snippet
                idx = content.lower().find(query)
                if idx == -1:  # Match in title but not content
                    idx = 0

                start = max(0, idx - 50)
                end = min(len(content), idx + 150)
                snippet = content[start:end].replace("\n", " ")

                results.append(
                    {
                        "title": page["title"],
                        "path": page["path"],
                        "snippet": f"...{snippet}...",
                    }
                )

        return results[:10]

    def get_all_pages(self) -> List[Dict[str, str]]:
        """Return a list of all indexed pages."""
        return self._pages
