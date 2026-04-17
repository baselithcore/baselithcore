"""Baselithbot plugin package.

Autonomous web navigation agent inspired by OpenClaw, layered on top of the
existing ``browser-agent`` Playwright backend. Adds stealth mode, sanitized
JS execution, and an explicit Observe -> Plan -> Act cognitive loop.
"""

from .agent import BaselithbotAgent
from .computer_use import AuditLogger, ComputerUseConfig, ComputerUseError
from .plugin import BaselithbotPlugin
from .types import BaselithbotResult, BaselithbotTask, StealthConfig

__all__ = [
    "BaselithbotAgent",
    "BaselithbotPlugin",
    "BaselithbotResult",
    "BaselithbotTask",
    "StealthConfig",
    "ComputerUseConfig",
    "ComputerUseError",
    "AuditLogger",
]
