"""Desktop agent — natural-language loop over Computer Use tools.

Observe -> Plan -> Act loop that lets a user describe an OS-level goal in
plain language (e.g. ``"open spotify and play my liked songs"``) and
dispatches the right primitives from the Computer Use tool surface
(desktop screenshots, shell launcher, mouse, keyboard, filesystem).

The agent is deliberately tool-agnostic: it receives a ``tool_map``
dictionary built by ``BaselithbotPlugin.build_computer_tool_map()`` and
only advertises the subset that the effective ``ComputerUseConfig``
actually allows. Every decision is a single JSON object produced by the
vision model (screenshot + textual history + tool catalog) so the agent
stays provider-agnostic across Anthropic / OpenAI / Ollama / LlamaCPP.
"""

from plugins.baselithbot.desktop_agent.agent import DesktopAgent
from plugins.baselithbot.desktop_agent.models import (
    DesktopStep,
    DesktopTaskResult,
    ProgressCallback,
)

__all__ = [
    "DesktopAgent",
    "DesktopStep",
    "DesktopTaskResult",
    "ProgressCallback",
]
