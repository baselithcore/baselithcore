"""
BaselithCore Plugins.

This package contains all official and community plugins for the framework.
"""

__path__ = __import__("pkgutil").extend_path(__path__, __name__)

# Verified plugin updates live in $BASELITH_PLUGIN_OVERLAY_DIR and must be
# registered before any plugins.<name> module is imported — see
# core/plugins/overlay.py. A failure here must never stop the framework booting.
try:
    from core.plugins.overlay import register_overlay_packages

    register_overlay_packages()
except Exception:  # silent-ok: logged below, bundled plugins still load
    import logging

    logging.getLogger(__name__).exception("plugin overlay registration failed")
