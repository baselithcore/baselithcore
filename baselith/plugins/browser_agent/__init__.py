"""Browser Agent plugin package."""

from baselith.core.plugins.compat_aliases import register_module_aliases

from .agent import BrowserAgent
from .plugin import BrowserAgentPlugin
from .tools import register_browser_tools
from .types import BrowserAction, BrowserActionType, BrowserAgentResult, PageState

__all__ = [
    "BrowserAgent",
    "BrowserAgentPlugin",
    "register_browser_tools",
    "BrowserAction",
    "BrowserActionType",
    "BrowserAgentResult",
    "PageState",
]

# The modules below moved into sub-packages (plugin layout standard); the old
# import paths stay importable so documented code and downstream plugins keep working.
register_module_aliases(
    {
        "_shortcuts": "agent.shortcuts",
        "actions": "agent.actions",
        "durable": "agent.durable",
        "ssrf": "agent.ssrf",
    },
    packages=(__name__,),
)
