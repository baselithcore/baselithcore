"""Document Sources plugin package."""

from baselith.core.plugins.compat_aliases import register_module_aliases

from .models import DocumentSourceError
from .plugin import DocumentSourcesPlugin

# The built-in sources register themselves with the registry when their module
# is imported. Nothing else imported them, so ``create_document_sources()``
# returned an empty list and every ``/reindex`` logged "No active document
# sources" over a populated DOCUMENTS_ROOT. ``web`` stays inert until
# WEB_DOCUMENTS_ENABLED and WEB_DOCUMENTS_URLS are set.
from .sources import filesystem as filesystem
from .sources import web as web
from .sources.registry import create_document_sources

__all__ = ["DocumentSourceError", "DocumentSourcesPlugin", "create_document_sources"]

# The modules below moved into sub-packages (plugin layout standard); the old
# import paths stay importable so documented code and downstream plugins keep working.
register_module_aliases(
    {
        "web": "sources.web",
        "filesystem": "sources.filesystem",
        "registry": "sources.registry",
        "labels": "sources.labels",
        "web_constants": "sources.web_constants",
        "web_guard": "sources.web_guard",
        "web_parser": "sources.web_parser",
        "docling_reader": "readers.docling",
        "ocr_backends": "readers.ocr_backends",
        "ocr_mineru": "readers.ocr_mineru",
    },
    packages=(__name__, "baselith.core.doc_sources"),
    unchanged=("models", "utils", "readers", "plugin"),
)
