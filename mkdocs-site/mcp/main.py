import asyncio
import sys
import logging
from core.observability.logging import get_logger
from pathlib import Path

# Add project root to path to ensure core and mkdocs-site are importable
current_file = Path(__file__).resolve()
# root is 2 levels up from mkdocs-site/mcp/main.py
project_root = current_file.parents[2]
if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

from core.mcp.server import MCPServer  # noqa: E402
from mcp.service import DocsService  # noqa: E402
from mcp.mcp import DocsMCPHandler  # noqa: E402

# Configure logging to stderr (Standard for MCP stdio)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = get_logger("docs-mcp")


async def main():
    """Run the Documentation MCP server."""
    # Initialize service
    # mkdocs-site root is 1 level up from mkdocs-site/mcp/
    docs_root = current_file.parent.parent
    service = DocsService(str(docs_root))
    await service.initialize()

    # Create server
    server = MCPServer(name="baselith-docs", version="1.0.0")

    # Register tools
    handler = DocsMCPHandler(service=service)
    handler.register_tools(server)

    logger.info(f"Starting Baselith Documentation MCP Server for {docs_root}")
    await server.run(transport="stdio")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error(f"Server error: {e}")
        sys.exit(1)
