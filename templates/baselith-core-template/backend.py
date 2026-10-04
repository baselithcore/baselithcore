"""The project's API server: the BaselithCore app, with this project's plugins.

Start it with ``baselith run`` (preflight checks, then uvicorn on HOST:PORT
from ``.env``) or ``uvicorn backend:app``. Settings come from ``.env`` in the
directory you start it from.
"""

from baselith import create_app

app = create_app()
