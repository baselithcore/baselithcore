"""Backward-compatible shim for Browser Agent plugin types."""

from baselith.plugins.browser_agent.types import (
    BrowserAction,
    BrowserActionType,
    BrowserAgentResult,
    PageState,
)

__all__ = ["BrowserAction", "BrowserActionType", "BrowserAgentResult", "PageState"]
