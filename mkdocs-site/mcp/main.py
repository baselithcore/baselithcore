"""Entry point of the Documentation MCP server (stdio).

Two launch forms are supported:

* ``python -m mcp.main`` with ``PYTHONPATH=<repo>/mkdocs-site`` — what IDE
  clients configured by hand use;
* ``python [-I] <repo>/mkdocs-site/mcp/main.py`` — the form a hardened stdio
  launcher accepts. ``baselith.core.mcp`` refuses ``PYTHONPATH`` in a server's
  environment and any ``-m`` module not on an allowlist, so an agent running on
  the framework (Lith, for one) can only start this server by script path.

Run as a script, Python puts this file's own directory on ``sys.path``, where
``mcp`` would resolve to the sibling ``mcp.py`` instead of this package. The
bootstrap below swaps it for the site root before anything is imported.
"""

import sys
from pathlib import Path

current_file = Path(__file__).resolve()
# mkdocs-site/ is the package root of ``mcp``; the repo root holds ``baselith``.
site_root = current_file.parents[1]
project_root = current_file.parents[2]
sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != current_file.parent]
for entry in (str(site_root), str(project_root)):
    if entry not in sys.path:
        sys.path.insert(0 if entry == str(site_root) else len(sys.path), entry)

import asyncio  # noqa: E402

from baselith.core.mcp.server import MCPServer  # noqa: E402
from baselith.core.observability.logging import (  # noqa: E402
    configure_logging,
    get_logger,
)
from mcp.mcp import DocsMCPHandler  # noqa: E402
from mcp.semantic import SemanticIndex, build_embedder, default_cache_dir  # noqa: E402
from mcp.service import DocsService  # noqa: E402

# stderr only: stdout carries the protocol, and a file sink would land in the
# working directory, which a hardened launcher sets to this script's own folder,
# so every session would write a ``logs/`` tree into the checkout.
configure_logging(stream=sys.stderr)
logger = get_logger("docs-mcp")


async def main():
    """Run the Documentation MCP server."""
    semantic = None
    embedder = build_embedder()
    if embedder is not None:
        semantic = SemanticIndex(*embedder, cache_dir=default_cache_dir())
    service = DocsService(str(site_root), semantic=semantic)
    await service.initialize()

    # Create server
    server = MCPServer(name="baselith-docs", version="1.0.0")

    # Register tools
    handler = DocsMCPHandler(service=service)
    handler.register_tools(server)

    logger.info(f"Starting Baselith Documentation MCP Server for {site_root}")
    await server.run(transport="stdio")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error(f"Server error: {e}")
        sys.exit(1)
