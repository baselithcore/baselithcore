"""Code editing layer (diff/patch + line-range edits + multi-file atomic).

All edits flow through ``ScopedFileSystem`` so they are subject to the same
``filesystem_root`` confinement and size limits as ordinary file writes.
"""

from .diff import apply_unified_diff
from .multi_file import MultiFileEdit, MultiFileEditor
from .patcher import LineRangeEdit, LineRangePatcher
from .search_replace import SearchReplaceEdit, apply_search_replace

__all__ = [
    "apply_unified_diff",
    "MultiFileEdit",
    "MultiFileEditor",
    "LineRangeEdit",
    "LineRangePatcher",
    "SearchReplaceEdit",
    "apply_search_replace",
]
