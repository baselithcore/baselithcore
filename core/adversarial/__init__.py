"""
Adversarial Testing Module

Provides automated security testing through adversarial agents.
Red team agents that probe for vulnerabilities, prompt injections,
and edge cases.

Key Concepts:
- RedTeamAgent: Agent that attacks other agents
- PromptFuzzer: Generates attack prompts
- HallucinationTrap: Tests for hallucinations
- BoundaryTester: Tests operational boundaries
"""

from .types import (
    AttackVector,
    AttackResult,
    Vulnerability,
    SecurityReport,
    AttackCategory,
    Severity,
    AttackStatus,
)
from .red_team import RedTeamAgent
from .fuzzer import PromptFuzzer
from .traps import HallucinationTrap
from .boundary import BoundaryTester

__all__ = [
    # Types
    "AttackVector",
    "AttackResult",
    "Vulnerability",
    "SecurityReport",
    "AttackCategory",
    "Severity",
    "AttackStatus",
    # Agents
    "RedTeamAgent",
    "PromptFuzzer",
    "HallucinationTrap",
    "BoundaryTester",
]
