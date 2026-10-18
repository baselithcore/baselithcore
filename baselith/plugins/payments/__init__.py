"""Payments plugin — reference PSP adapter for the AP2 payment seam."""

from baselith.core.plugins.compat_aliases import register_module_aliases

from .adapters.mock_psp import MockPSPAdapter
from .plugin import PaymentsPlugin

__all__ = [
    "MockPSPAdapter",
    "PaymentsPlugin",
]

# The modules below moved into sub-packages (plugin layout standard); the old
# import paths stay importable so documented code and downstream plugins keep working.
register_module_aliases(
    {
        "mock_psp": "adapters.mock_psp",
    },
    packages=(__name__,),
)
