"""
Browser Agent Types.

Data structures and enums used by the Browser Agent.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class BrowserActionType(str, Enum):
    """Types of browser actions."""

    NAVIGATE = "navigate"
    CLICK = "click"
    TYPE = "type"
    SCROLL = "scroll"
    WAIT = "wait"
    SCREENSHOT = "screenshot"
    EXTRACT = "extract"
    DONE = "done"
    FAIL = "fail"


@dataclass
class BrowserAction:
    """Represents a browser action to execute."""

    action_type: BrowserActionType
    selector: str | None = None
    value: str | None = None
    coordinates: tuple[int, int] | None = None
    reasoning: str = ""


@dataclass
class PageState:
    """Current state of a web page."""

    url: str
    title: str
    screenshot_base64: str
    viewport_width: int
    viewport_height: int
    visible_text: str = ""


@dataclass
class BrowserAgentResult:
    """Result of a browser agent task."""

    success: bool
    final_url: str
    steps_taken: int
    extracted_data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    screenshots: list[str] = field(default_factory=list)


__all__ = [
    "BrowserActionType",
    "BrowserAction",
    "PageState",
    "BrowserAgentResult",
]
