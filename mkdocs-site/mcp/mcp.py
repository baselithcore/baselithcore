import json
from typing import Any

from core.mcp.server import MCPServer

from .service import DocsService

# Every tool here reads markdown off disk and nothing else. Declaring the
# category matters twice over: ``MCPServer`` defaults tools to ``destructive``,
# which the fail-closed autonomy policy refuses to run over a transport with no
# approval channel, and the category is what ``readOnlyHint`` is derived from in
# the tool listing clients see.
_READ_ONLY = "read_only"


class DocsMCPHandler:
    """Handler for Documentation MCP tools."""

    def __init__(self, service: DocsService):
        self.service = service
        self._cached_all_docs: str | None = None
        self._cached_all_docs_revision: int | None = None

    def register_tools(self, server: MCPServer):
        """Register documentation tools and resources to the MCP server."""

        @server.tool(
            name="search_docs",
            description="Search the project documentation with ranked results and snippets",
            category=_READ_ONLY,
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keywords or phrase to search for",
                    }
                },
                "required": ["query"],
            },
        )
        async def search_docs(query: str) -> list[dict[str, Any]]:
            """Search documentation."""
            return await self.service.search(query)

        @server.tool(
            name="get_doc_page",
            description="Retrieve the full content of a documentation page by its file path",
            category=_READ_ONLY,
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path (e.g. 'getting-started/installation.md')",
                    }
                },
                "required": ["path"],
            },
        )
        async def get_doc_page(path: str) -> str:
            """Read a specific doc page."""
            content = await self.service.get_page_content(path)
            return content or f"Error: Page not found at {path}"

        @server.tool(
            name="get_doc_by_title",
            description="Find and retrieve a documentation page by its title (exact or partial)",
            category=_READ_ONLY,
            input_schema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "The title of the page to find",
                    }
                },
                "required": ["title"],
            },
        )
        async def get_doc_by_title(title: str) -> dict[str, str]:
            """Retrieve page by title."""
            result = await self.service.get_page_by_title(title)
            return result or {"error": f"No page found with title: {title}"}

        @server.tool(
            name="get_nav",
            description="Get the hierarchical navigation structure of the documentation",
            category=_READ_ONLY,
            input_schema={"type": "object", "properties": {}},
        )
        async def get_nav() -> list[Any]:
            """Get navigation tree."""
            return self.service.get_nav_tree()

        @server.tool(
            name="list_docs",
            description=(
                "List every documentation page as a flat list of paths with "
                "their full breadcrumb titles"
            ),
            category=_READ_ONLY,
            input_schema={"type": "object", "properties": {}},
        )
        async def list_docs() -> list[dict[str, str]]:
            """List all doc pages."""
            return self.service.get_all_pages()

        @server.tool(
            name="get_docs_batch",
            description="Retrieve the full content of multiple documentation pages in a single call",
            category=_READ_ONLY,
            input_schema={
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of relative paths to retrieve",
                    }
                },
                "required": ["paths"],
            },
        )
        async def get_docs_batch(paths: list[str]) -> dict[str, str]:
            """Batch retrieve pages."""
            return await self.service.get_docs_batch(paths)

        @server.tool(
            name="get_docs_summary",
            description="List all available documentation pages with titles and introductory summaries",
            category=_READ_ONLY,
            input_schema={"type": "object", "properties": {}},
        )
        async def get_docs_summary() -> list[dict[str, str]]:
            """Get all summaries."""
            return self.service.get_docs_summary()

        @server.tool(
            name="find_related_pages",
            description="Find documentation pages related to a specific file based on content similarity",
            category=_READ_ONLY,
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The path of the document to find relations for",
                    }
                },
                "required": ["path"],
            },
        )
        async def find_related_pages(path: str) -> list[dict[str, Any]]:
            """Find relations."""
            return self.service.find_related_pages(path)

        @server.tool(
            name="search_in_section",
            description="Search documentation restricted to a specific section (e.g., 'Architecture')",
            category=_READ_ONLY,
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "section": {
                        "type": "string",
                        "description": "Name of the section to search in",
                    },
                },
                "required": ["query", "section"],
            },
        )
        async def search_in_section(query: str, section: str) -> list[dict[str, Any]]:
            """Restricted search."""
            return await self.service.search_in_section(query, section)

        # --- Resources ---

        @server.resource(
            uri="mcp://docs/navigation",
            name="Documentation Navigation",
            description="The full hierarchical structure of the documentation site",
            mime_type="application/json",
        )
        async def get_docs_nav_resource(uri: str) -> str:
            return json.dumps(self.service.get_nav_tree(), indent=2)

        @server.resource(
            uri="mcp://docs/all",
            name="Full Documentation",
            description="All documentation pages combined into a single text resource",
            mime_type="text/markdown",
        )
        async def get_all_docs_resource(uri: str) -> str:
            # Rebuild whenever any page changed on disk: the concatenation is
            # expensive enough to cache, and stale enough to be useless if the
            # cache never expires.
            if (
                self._cached_all_docs is not None
                and self._cached_all_docs_revision == self.service.revision
            ):
                return self._cached_all_docs

            pages = self.service.get_all_pages()
            combined = []
            for page in pages:
                content = await self.service.get_page_content(page["path"])
                title = page.get("title", "Untitled")
                combined.append(f"# {title}\n\n{content}\n\n---\n")

            self._cached_all_docs = "\n".join(combined)
            self._cached_all_docs_revision = self.service.revision
            return self._cached_all_docs
