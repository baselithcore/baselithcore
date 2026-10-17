"""
Reasoning Agent Plugin.

Provides complex problem solving via Tree of Thoughts.
"""

from baselith.core.plugins.compat_aliases import register_module_aliases

# The modules below moved into sub-packages (plugin layout standard); the old
# import paths stay importable so documented code and downstream plugins keep working.
register_module_aliases(
    {
        "reasoning_agent": "agent",
    },
    packages=(__name__,),
)
