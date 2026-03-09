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
        """Register documentation tools to the MCP server."""

        @server.tool(
            name="search_docs",
            description="Search the project documentation for specific information",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keywords or phrase to search for in the documentation",
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
            description="Retrieve the full content of a documentation page by its path",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path of the page (e.g. 'getting-started/installation.md')",
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
            name="list_docs",
            description="List all available documentation pages",
            input_schema={"type": "object", "properties": {}},
        )
        async def list_docs() -> List[Dict[str, str]]:
            """List all doc pages."""
            return self.service.get_all_pages()
