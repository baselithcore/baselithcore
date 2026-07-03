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

from .boundary import BoundaryTester
from .fuzzer import PromptFuzzer
from .red_team import RedTeamAgent
from .traps import HallucinationTrap
from .types import (
    AttackCategory,
    AttackResult,
    AttackStatus,
    AttackVector,
    SecurityReport,
    Severity,
    Vulnerability,
)

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
