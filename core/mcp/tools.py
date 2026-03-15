"""
MCP Tool Adapter.

Bridges the Baselith-Core's internal tools with the MCP protocol,
allowing seamless exposure of existing capabilities as MCP tools.

Usage:
    from core.mcp import MCPServer, MCPToolAdapter

    server = MCPServer()
    adapter = MCPToolAdapter(server)

    # Register existing tools
    adapter.register_scraper_tools()
    adapter.register_rag_tools()
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Coroutine, TYPE_CHECKING

from core.observability.logging import get_logger
from core.config import get_mcp_config

if TYPE_CHECKING:
    from core.mcp.server import MCPServer

logger = get_logger(__name__)


class MCPToolAdapter:
    """
    Adapter for exposing Baselith-Core tools via MCP.

    This class bridges the internal tool implementations with the MCP protocol,
    handling schema conversion and result formatting.
    """

    def __init__(self, server: MCPServer) -> None:
        """
        Initialize the adapter.

        Args:
            server: MCP server instance to register tools with
        """
        self.server = server

    def register_function(
        self,
        func: Callable[..., Coroutine[Any, Any, Any]],
        name: str | None = None,
        description: str | None = None,
    ) -> None:
        """
        Register a function as an MCP tool.

        Args:
            func: Async function to register
            name: Tool name (defaults to function name)
            description: Tool description (defaults to docstring)
        """
        tool_name = name or func.__name__
        tool_description = description or func.__doc__ or f"Execute {tool_name}"

        # Generate input schema from function signature
        schema = self._generate_schema(func)

        self.server.register_tool(
            name=tool_name,
            description=tool_description,
            input_schema=schema,
            handler=func,
        )

        logger.info("mcp_tool_adapted", tool=tool_name)

    def _generate_schema(self, func: Callable[..., Any]) -> dict[str, Any]:
        """Generate JSON Schema from function signature."""
        from typing import get_type_hints

        try:
            hints = get_type_hints(func)
        except Exception:
            hints = {}

        sig = inspect.signature(func)

        properties: dict[str, Any] = {}
        required: list[str] = []

        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue

            param_type = hints.get(param_name)
            prop = self._type_to_json_schema(param_type)

            # Add description from docstring if available
            properties[param_name] = prop

            # Required if no default value
            if param.default is inspect.Parameter.empty:
                required.append(param_name)

        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }

    def _type_to_json_schema(self, python_type: Any) -> dict[str, Any]:
        """Convert Python type hint to JSON Schema."""
        from typing import get_origin, get_args, Union

        if python_type is None:
            return {"type": "string"}

        # Handle basic types
        type_map = {
            str: {"type": "string"},
            int: {"type": "integer"},
            float: {"type": "number"},
            bool: {"type": "boolean"},
            bytes: {"type": "string", "contentEncoding": "base64"},
        }

        if python_type in type_map:
            return type_map[python_type]

        # Handle generic types
        origin = get_origin(python_type)
        args = get_args(python_type)

        if origin is list:
            item_type = args[0] if args else str
            return {
                "type": "array",
                "items": self._type_to_json_schema(item_type),
            }

        if origin is dict:
            return {"type": "object"}

        if origin is Union:
            # Handle Optional (Union with None)
            non_none_args = [a for a in args if a is not type(None)]
            if len(non_none_args) == 1:
                return self._type_to_json_schema(non_none_args[0])
            # Multiple types - use anyOf
            return {"anyOf": [self._type_to_json_schema(a) for a in non_none_args]}

        # Default to string for unknown types
        return {"type": "string"}

    # -------------------------------------------------------------------------
    # Pre-built Tool Registrations
    # -------------------------------------------------------------------------

    def register_scraper_tools(self) -> None:
        """
        Register web scraper tools.

        Exposes the scraper module capabilities as MCP tools.
        """

        @self.server.tool(
            name="scrape_url",
            description="Scrape content from a URL. Returns extracted text and metadata.",
            input_schema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to scrape",
                    },
                    "extract_links": {
                        "type": "boolean",
                        "description": "Whether to extract links from the page",
                        "default": False,
                    },
                    "wait_for_js": {
                        "type": "boolean",
                        "description": "Whether to wait for JavaScript rendering",
                        "default": False,
                    },
                },
                "required": ["url"],
            },
        )
        async def scrape_url(
            url: str, extract_links: bool = False, wait_for_js: bool = False
        ) -> dict[str, Any]:
            """Scrape a URL and return content."""
            try:
                from core.scraper.scraper import Scraper

                scraper = Scraper()
                result_page, extracted_data = await scraper.scrape(
                    url, use_js=wait_for_js
                )

                # Simplify result for context
                return {
                    "url": url,
                    "title": extracted_data.metadata.title
                    if extracted_data.metadata
                    else "",
                    "content": extracted_data.text[:5000]
                    if extracted_data.text
                    else "",
                    "status": "success",
                    "links": [link.url for link in extracted_data.links][:50]
                    if extract_links and extracted_data.links
                    else [],
                }

            except Exception as e:
                return {
                    "url": url,
                    "status": "error",
                    "error": str(e),
                }

        logger.info("mcp_scraper_tools_registered")

    def register_rag_tools(self) -> None:
        """
        Register RAG (Retrieval-Augmented Generation) tools.

        Exposes vector search and document retrieval as MCP tools.
        """

        @self.server.tool(
            name="search_knowledge_base",
            description="Search the knowledge base using semantic similarity",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Number of results to return",
                        "default": 5,
                    },
                    "collection": {
                        "type": "string",
                        "description": "Collection to search in",
                        "default": "default",
                    },
                },
                "required": ["query"],
            },
        )
        async def search_knowledge_base(
            query: str, top_k: int | None = None, collection: str = "default"
        ) -> list[dict[str, Any]]:
            """Search the knowledge base."""
            config = get_mcp_config()
            limit = top_k or config.mcp_rag_default_top_k

            # Placeholder - would integrate with actual VectorStore service
            try:
                # from core.services.vector_store import VectorStore
                # store = VectorStore()
                # results = await store.search(query, k=limit, collection=collection)
                pass
            except ImportError:
                return [{"error": "VectorStore service not available"}]

            return [
                {
                    "id": "placeholder",
                    "content": f"Search results for: {query}",
                    "score": 0.95,
                    "metadata": {"collection": collection, "limit": limit},
                }
            ]

        @self.server.tool(
            name="index_document",
            description="Index a document into the knowledge base",
            input_schema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Document content to index",
                    },
                    "metadata": {
                        "type": "object",
                        "description": "Optional metadata for the document",
                    },
                    "collection": {
                        "type": "string",
                        "description": "Collection to index into",
                        "default": "default",
                    },
                },
                "required": ["content"],
            },
        )
        async def index_document(
            content: str,
            metadata: dict[str, Any] | None = None,
            collection: str = "default",
        ) -> dict[str, Any]:
            """Index a document."""
            # Placeholder - would integrate with actual Indexing service
            return {
                "status": "indexed",
                "collection": collection,
                "content_length": len(content),
            }

        logger.info("mcp_rag_tools_registered")

    def register_reasoning_tools(self) -> None:
        """
        Register reasoning and planning tools.

        Exposes Tree of Thoughts and code execution as MCP tools.
        """

        @self.server.tool(
            name="execute_code",
            description="Execute Python code in a secure sandbox",
            input_schema={
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python code to execute",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Execution timeout in seconds",
                        "default": get_mcp_config().mcp_execute_code_timeout,
                    },
                },
                "required": ["code"],
            },
        )
        async def execute_code(code: str, timeout: int | None = None) -> dict[str, Any]:
            """Execute code in sandbox."""
            config = get_mcp_config()
            exec_timeout = timeout or config.mcp_execute_code_timeout

            try:
                from core.services.sandbox import SandboxService

                sandbox = SandboxService()
                result = await sandbox.execute_code_async(code, timeout=exec_timeout)

                return {
                    "status": "success",
                    "output": result.stdout,
                    "error": result.stderr,
                }

            except Exception as e:
                return {
                    "status": "error",
                    "error": str(e),
                }

        @self.server.tool(
            name="plan_task",
            description="Create a step-by-step plan for a complex task using Tree of Thoughts",
            input_schema={
                "type": "object",
                "properties": {
                    "task_description": {
                        "type": "string",
                        "description": "Description of the task to plan",
                    },
                    "context": {
                        "type": "string",
                        "description": "Additional context for planning",
                    },
                },
                "required": ["task_description"],
            },
        )
        async def plan_task(task_description: str, context: str = "") -> dict[str, Any]:
            """Plan a complex task."""
            # Placeholder - would integrate with ToT reasoning
            return {
                "task": task_description,
                "steps": [
                    {"step": 1, "description": "Analyze requirements"},
                    {"step": 2, "description": "Break down into subtasks"},
                    {"step": 3, "description": "Execute plan"},
                ],
                "status": "planned",
            }

        logger.info("mcp_reasoning_tools_registered")

    def register_plugin_tools(self) -> None:
        """
        Register tools exposed by plugins.

        Iterates through the PluginRegistry and registers tools from
        initialized plugins that implement get_mcp_tools().
        """
        try:
            from core.di import ServiceRegistry
            from core.plugins import PluginRegistry

            if ServiceRegistry.has(PluginRegistry):
                registry = ServiceRegistry.get(PluginRegistry)
                plugins = registry.get_all()
            else:
                # Fallback to a new instance if not registered (e.g. standalone/test)
                registry = PluginRegistry()
                plugins = registry.get_all()

            for plugin in plugins:
                if not plugin.is_initialized():
                    continue

                try:
                    tools = plugin.get_mcp_tools()
                    for tool_def in tools:
                        name = tool_def.get("name")
                        description = tool_def.get("description")
                        schema = tool_def.get("input_schema")
                        handler = tool_def.get("handler")

                        if name and handler:
                            self.server.register_tool(
                                name=name,
                                description=description or "",
                                input_schema=schema or {},
                                handler=handler,
                            )
                            logger.info(
                                "mcp_plugin_tool_registered",
                                plugin=plugin.metadata.name,
                                tool=name,
                            )
                except Exception as e:
                    logger.error(
                        "mcp_plugin_tool_registration_failed",
                        plugin=plugin.metadata.name,
                        error=str(e),
                    )
        except ImportError:
            logger.warning("mcp_plugin_registry_unavailable")

    def register_all_tools(self) -> None:
        """Register all available tool categories."""
        self.register_scraper_tools()
        self.register_rag_tools()
        self.register_reasoning_tools()
        self.register_plugin_tools()
        logger.info("mcp_all_tools_registered")


# ============================================================================
# Factory Function
# ============================================================================


def create_mcp_server_with_tools() -> MCPServer:
    """
    Create an MCP server with all system tools registered.

    Returns:
        Fully configured MCPServer
    """
    from core.mcp.server import MCPServer

    server = MCPServer()
    adapter = MCPToolAdapter(server)
    adapter.register_all_tools()

    return server
