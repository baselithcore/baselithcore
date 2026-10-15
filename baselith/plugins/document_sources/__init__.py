"""Document Sources plugin package."""

# The built-in sources register themselves with the registry when their module
# is imported. Nothing else imported them, so ``create_document_sources()``
# returned an empty list and every ``/reindex`` logged "No active document
# sources" over a populated DOCUMENTS_ROOT. ``web`` stays inert until
# WEB_DOCUMENTS_ENABLED and WEB_DOCUMENTS_URLS are set.
from . import filesystem as filesystem
from . import web as web
from .models import DocumentSourceError
from .plugin import DocumentSourcesPlugin
from .registry import create_document_sources

__all__ = ["DocumentSourceError", "DocumentSourcesPlugin", "create_document_sources"]
