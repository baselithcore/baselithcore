import asyncio
import sys
from pathlib import Path

# Add project root to path
current_file = Path(__file__).resolve()
project_root = current_file.parents[2]
if str(project_root) not in sys.path:
    sys.path.append(str(project_root))

# No need to add mkdocs-site to path if we run with python -m
from mcp.service import DocsService  # noqa: E402


async def test_service():
    docs_root = current_file.parent.parent
    print(f"Testing DocsService with root: {docs_root}")

    service = DocsService(str(docs_root))
    await service.initialize()

    pages = service.get_all_pages()
    print(f"\nFound {len(pages)} pages in zensical.toml")
    if pages:
        print(f"First page: {pages[0]}")

    # Test search
    query = "installation"
    print(f"\nSearching for: '{query}'")
    results = await service.search(query)
    print(f"Found {len(results)} results")
    for r in results:
        print(f"- {r['title']} ({r['path']})")

    # Test get content
    if pages:
        path = pages[0]["path"]
        print(f"\nGetting content for: {path}")
        content = await service.get_page_content(path)
        if content:
            print(f"Content length: {len(content)}")
            print(f"First 100 chars: {content[:100]}...")
        else:
            print("Error: Could not retrieve content")


if __name__ == "__main__":
    asyncio.run(test_service())
