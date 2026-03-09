"""
Agents Module.

Contains specialized agents for autonomous tasks:
- BrowserAgent: Web automation with visual reasoning
- CodingAgent: Code generation, debugging, and testing
"""

from core.agents.browser_agent import BrowserAgent
from core.agents.coding.agent import CodingAgent

__all__ = [
    "BrowserAgent",
    "CodingAgent",
]
