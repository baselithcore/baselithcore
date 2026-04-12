"""
Console Router.

Serves the frontend SPA (Single Page Application) for the BaselithCore Console.
Routes all paths under /console to the main index.html file, delegating
the actual navigation and view rendering to the client-side router.
"""

from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(tags=["console"])

CONSOLE_INDEX_PATH = Path("core/static/frontend/index.html")


@router.get("/console", response_class=FileResponse)
@router.get("/console/{full_path:path}", response_class=FileResponse)
async def serve_console(full_path: str = ""):
    """
    Serve the main entry point for the frontend SPA.
    Any path under /console/ is redirected to the index.html to allow
    client-side routing to take over.
    """
    if not CONSOLE_INDEX_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail="Console frontend not found. Ensure the frontend has been built to core/static/frontend.",
        )
    return FileResponse(CONSOLE_INDEX_PATH)
