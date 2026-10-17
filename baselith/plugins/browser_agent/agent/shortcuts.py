"""One-action shortcuts of :class:`~baselith.plugins.browser_agent.agent.BrowserAgent`.

Kept apart so the agent module stays under the file-size cap; each shortcut is
a single :meth:`execute_action` or :meth:`get_page_state` call.
"""

from __future__ import annotations

from typing import Any

from ..types import BrowserAction, BrowserActionType


class BrowserShortcutsMixin:
    """``screenshot``, ``click`` and ``type_text`` over the agent's primitives."""

    execute_action: Any
    get_page_state: Any

    async def screenshot(self) -> str:
        """Take a screenshot and return base64."""
        state = await self.get_page_state()
        return state.screenshot_base64

    async def click(self, selector: str) -> bool:
        """Click an element by selector."""
        return await self.execute_action(
            BrowserAction(action_type=BrowserActionType.CLICK, selector=selector)
        )

    async def type_text(self, selector: str, text: str) -> bool:
        """Type text into an element."""
        return await self.execute_action(
            BrowserAction(
                action_type=BrowserActionType.TYPE,
                selector=selector,
                value=text,
            )
        )
