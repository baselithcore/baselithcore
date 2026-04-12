# core/scraper/storage/__init__.py
"""Storage backends for the web scraper module."""

from .base import BaseStorage
from .filesystem import FilesystemStorage
from .memory import MemoryStorage

__all__ = [
    "BaseStorage",
    "MemoryStorage",
    "FilesystemStorage",
]
