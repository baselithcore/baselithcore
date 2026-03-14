from typing import Any, Dict, List

# We assume core.mcp.server exists based on the EXTERNAL reference
try:
    from core.mcp.server import MCPServer
except ImportError:
    # Fallback/Mock if not yet in core - though we should ideally use core
    # For now, let's assume it's there as per EXTERNAL examples.
    import sys

    print(
        "Error: core.mcp.server not found. Ensure core modules are correctly installed.",
        file=sys.stderr,
    )
    raise

from .service import DocsService


class DocsMCPHandler:
    """Handler for Documentation MCP tools."""

    def __init__(self, service: DocsService):
        self.service = service

    def register_tools(self, server: MCPServer):
        """Register documentation tools and resources to the MCP server."""

        @server.tool(
            name="search_docs",
            description="Search the project documentation with ranked results and snippets",
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
        async def search_docs(query: str) -> List[Dict[str, Any]]:
            """Search documentation."""
            return await self.service.search(query)

        @server.tool(
            name="get_doc_page",
            description="Retrieve the full content of a documentation page by its file path",
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
        async def get_doc_by_title(title: str) -> Dict[str, str]:
            """Retrieve page by title."""
            result = await self.service.get_page_by_title(title)
            return result or {"error": f"No page found with title: {title}"}

        @server.tool(
            name="get_nav",
            description="Get the hierarchical navigation structure of the documentation",
            input_schema={"type": "object", "properties": {}},
        )
        async def get_nav() -> List[Any]:
            """Get navigation tree."""
            return self.service.get_nav_tree()

        @server.tool(
            name="list_docs",
            description="List all available documentation pages as a flat list",
            input_schema={"type": "object", "properties": {}},
        )
        async def list_docs() -> List[Dict[str, str]]:
            """List all doc pages."""
            return self.service.get_all_pages()

        # --- Resources ---

        @server.resource(
            uri="mcp://docs/navigation",
            name="Documentation Navigation",
            description="The full hierarchical structure of the documentation site",
            mime_type="application/json",
        )
        async def get_docs_nav_resource(uri: str) -> str:
            import json

            return json.dumps(self.service.get_nav_tree(), indent=2)

        @server.resource(
            uri="mcp://docs/all",
            name="Full Documentation",
            description="All documentation pages combined into a single text resource",
            mime_type="text/markdown",
        )
        async def get_all_docs_resource(uri: str) -> str:
            pages = self.service.get_all_pages()
            combined = []
            for page in pages:
                content = await self.service.get_page_content(page["path"])
                combined.append(f"# {page['title']}\n\n{content}\n\n---\n")
            return "\n".join(combined)
