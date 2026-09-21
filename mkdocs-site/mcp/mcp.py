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

_PATH_DESC = "Page path relative to docs/, e.g. 'core-modules/memory.md'"


class DocsMCPHandler:
    """Handler for Documentation MCP tools.

    The tool surface is deliberately small. Every definition is resident in the
    client's context for the whole session, and a tool that invites an
    expensive call costs more than the one it replaces: retrieval here is meant
    to go search -> section, not search -> whole page.
    """

    def __init__(self, service: DocsService):
        self.service = service

    def register_tools(self, server: MCPServer) -> None:
        """Register documentation tools and resources to the MCP server."""

        @server.tool(
            name="search_docs",
            description=(
                "Search the documentation. Returns ranked hits with the "
                "heading each match sits under, to read with get_doc_section."
            ),
            category=_READ_ONLY,
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keywords or phrase"},
                    "section": {
                        "type": "string",
                        "description": "Restrict to a nav section, e.g. 'Core Modules'",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum hits (default 5, max 20)",
                    },
                },
                "required": ["query"],
            },
        )
        async def search_docs(
            query: str, section: str | None = None, limit: int = 5
        ) -> list[dict[str, Any]]:
            """Search documentation."""
            return await self.service.search(query, section=section, limit=limit)

        @server.tool(
            name="get_doc_section",
            description=(
                "Read one section of a page by its heading or anchor, "
                "subsections included. Prefer this over get_doc_page."
            ),
            category=_READ_ONLY,
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": _PATH_DESC},
                    "heading": {
                        "type": "string",
                        "description": "Heading text or anchor from a search hit",
                    },
                },
                "required": ["path", "heading"],
            },
        )
        async def get_doc_section(path: str, heading: str) -> dict[str, Any]:
            """Read one section of a page."""
            result = self.service.get_section(path, heading)
            if result is None:
                return {
                    "error": f"No section matching '{heading}' in {path}",
                    "hint": "Call get_doc_outline for the headings this page has.",
                }
            return result

        @server.tool(
            name="get_doc_outline",
            description="List a page's headings with their sizes, without its body",
            category=_READ_ONLY,
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": _PATH_DESC},
                },
                "required": ["path"],
            },
        )
        async def get_doc_outline(path: str) -> dict[str, Any]:
            """Headings of a page."""
            return self.service.get_outline(path) or {
                "error": f"Page not found at {path}"
            }

        @server.tool(
            name="get_doc_page",
            description=(
                "Read a whole page. Pages over the size budget return their "
                "outline instead unless full=true."
            ),
            category=_READ_ONLY,
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": _PATH_DESC},
                    "full": {
                        "type": "boolean",
                        "description": "Return the body even when it is large",
                    },
                },
                "required": ["path"],
            },
        )
        async def get_doc_page(path: str, full: bool = False) -> dict[str, Any]:
            """Read a specific doc page."""
            return self.service.get_page(path, full=full) or {
                "error": f"Page not found at {path}"
            }

        @server.tool(
            name="get_docs_batch",
            description="Read several pages in one call, under the same size budget",
            category=_READ_ONLY,
            input_schema={
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Relative page paths",
                    },
                    "full": {
                        "type": "boolean",
                        "description": "Return bodies even when they are large",
                    },
                },
                "required": ["paths"],
            },
        )
        async def get_docs_batch(
            paths: list[str], full: bool = False
        ) -> dict[str, Any]:
            """Batch retrieve pages."""
            return await self.service.get_docs_batch(paths, full=full)

        @server.tool(
            name="list_docs",
            description=(
                "List page paths with their breadcrumb titles, optionally "
                "only one nav section"
            ),
            category=_READ_ONLY,
            input_schema={
                "type": "object",
                "properties": {
                    "section": {
                        "type": "string",
                        "description": "Nav section, e.g. 'Getting Started'",
                    },
                },
            },
        )
        async def list_docs(section: str | None = None) -> list[dict[str, str]]:
            """List doc pages."""
            return self.service.list_pages(section)

        @server.tool(
            name="find_related_pages",
            description="Pages whose vocabulary overlaps a given page",
            category=_READ_ONLY,
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": _PATH_DESC},
                },
                "required": ["path"],
            },
        )
        async def find_related_pages(path: str) -> list[dict[str, Any]]:
            """Find relations."""
            return self.service.find_related_pages(path)

        # --- Resources ---

        # The navigation tree is the one whole-site artefact small enough to
        # hand over at once. A resource concatenating every page — which this
        # server used to expose — is over half a million tokens: nothing can
        # read it, and offering it only invites the attempt.
        @server.resource(
            uri="mcp://docs/navigation",
            name="Documentation Navigation",
            description="The full hierarchical structure of the documentation site",
            mime_type="application/json",
        )
        async def get_docs_nav_resource(uri: str) -> str:
            return json.dumps(self.service.get_nav_tree(), indent=2)
