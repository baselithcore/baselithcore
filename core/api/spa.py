"""
SPA-aware static file serving.

Plugin frontends are client-side-routed single-page apps (React Router /
Next.js) mounted at ``/<plugin>``. A plain :class:`starlette.staticfiles.StaticFiles`
only resolves the directory root to ``index.html``; any deep link
(``/auth/account``, ``/auth/reset-password``, an emailed verify-email URL, or
simply a browser reload on a client route) asks the server for a file that does
not exist on disk and gets a hard 404.

:class:`SPAStaticFiles` adds the standard history-API fallback: an unmatched
*non-asset* path is served ``index.html`` so the client router can take over.
Real missing assets (paths whose final segment has a file extension, e.g.
``/assets/app.js``) still 404 honestly instead of silently returning HTML.
"""

from __future__ import annotations

from typing import Any

from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles

__all__ = ["SPAStaticFiles"]


class SPAStaticFiles(StaticFiles):
    """:class:`StaticFiles` that falls back to ``index.html`` on deep links."""

    async def get_response(self, path: str, scope: Any) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            # Only rescue genuine client-route deep links. A request for a
            # concrete asset (last segment has an extension) that is missing is
            # a real 404 and must stay one — masking it with HTML hides bugs.
            if exc.status_code == 404 and "." not in path.rsplit("/", 1)[-1]:
                return await super().get_response("index.html", scope)
            raise
