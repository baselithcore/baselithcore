"""Namespace migration codemod: ``core``/``plugins`` → ``baselith.core``/``baselith.plugins``.

Entry point: ``scripts/migrate_namespace.py``. Design and runbook:
``docs/superpowers/specs/2026-10-10-baselith-namespace-1.0-design.md``.
"""

from .config import NEW_ROOT, OLD_TOPS, classify
from .engine import run
from .index import ModuleIndex
from .report import FileResult, Report
from .text_rules import TextContext, rewrite_literal, rewrite_text

__all__ = [
    "NEW_ROOT",
    "OLD_TOPS",
    "FileResult",
    "ModuleIndex",
    "Report",
    "TextContext",
    "classify",
    "rewrite_literal",
    "rewrite_text",
    "run",
]
