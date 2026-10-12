"""Shared helpers for the stdio escape corpus."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

FAKE = Path(__file__).parent / "fakes" / "stdio_server.py"
POSIX = pytest.mark.skipif(os.name == "nt", reason="POSIX modes and symlinks")


def exe(directory: Path, name: str, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    """Create an executable file *name* in *directory* (made if missing)."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(body)
    path.chmod(0o755)
    return path
