"""``core.api.lifespan`` must not drag in dependencies nothing uses.

``fastapi-limiter`` used to be initialised at startup (opening a Redis client)
although no route ever applied one of its dependencies — the framework's own
``RateLimiter`` (``core.middleware.rate_limiter``) is the limiter. Importing the
lifespan must therefore not import the package at all; if it does, the dead
dependency crept back in.
"""

from __future__ import annotations

import subprocess
import sys

_PROBE = """
import sys
import core.api.lifespan  # noqa: F401
print("fastapi_limiter" in sys.modules)
"""


def test_lifespan_does_not_import_fastapi_limiter():
    completed = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        check=True,
        timeout=110,
    )
    assert completed.stdout.strip().splitlines()[-1] == "False"
