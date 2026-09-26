"""
Agents Module.

Contains specialized agents for autonomous tasks:
- BrowserAgent: Web automation with visual reasoning
- CodingAgent: Code generation, debugging, and testing

Deprecated (``core.stability`` tier ``deprecated``): the maintained
implementations are the ``browser_agent`` and ``coding_agent`` plugins.
Importing this package emits a ``DeprecationWarning``.
"""

import warnings

from core.agents.browser_agent import BrowserAgent
from core.agents.coding.agent import CodingAgent
from core.stability import _deprecation_message

# Module-level twin of ``@deprecated``: the decorator wraps symbols, and these
# classes are still imported by code that must keep working until removal.
warnings.warn(
    _deprecation_message(
        "core.agents",
        since="0.39",
        removed_in="1.0",
        alternative="plugins.browser_agent / plugins.coding_agent",
    ),
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "BrowserAgent",
    "CodingAgent",
]
