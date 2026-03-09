"""
Adversarial Testing Types

Core data structures for security testing.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional
from datetime import datetime
import uuid


class AttackCategory(Enum):
    """Categories of attack vectors."""

    PROMPT_INJECTION = "prompt_injection"
    JAILBREAK = "jailbreak"
    DATA_EXTRACTION = "data_extraction"
    HALLUCINATION = "hallucination"
    BOUNDARY_VIOLATION = "boundary_violation"
    DENIAL_OF_SERVICE = "denial_of_service"
    PRIVILEGE_ESCALATION = "privilege_escalation"


class Severity(Enum):
    """Vulnerability severity levels."""

    INFO = 1
    LOW = 2
    MEDIUM = 3
    HIGH = 4
    CRITICAL = 5


class AttackStatus(Enum):
    """Status of an attack attempt."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    BLOCKED = "blocked"
    DETECTED = "detected"


@dataclass
class AttackVector:
    """
    An attack vector/technique to test.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    category: AttackCategory = AttackCategory.PROMPT_INJECTION
    description: str = ""
    payload: str = ""
    expected_behavior: str = ""
    severity_if_successful: Severity = Severity.MEDIUM
    metadata: Dict = field(default_factory=dict)

    @property
    def is_injection(self) -> bool:
        """Check if this is an injection attack."""
        return self.category in [
            AttackCategory.PROMPT_INJECTION,
            AttackCategory.JAILBREAK,
        ]


@dataclass
class AttackResult:
    """
    Result of an attack attempt.
    """

    attack_vector: AttackVector
    status: AttackStatus = AttackStatus.PENDING
    success: bool = False
    response: str = ""
    detection_triggered: bool = False
    execution_time: float = 0.0
    evidence: List[str] = field(default_factory=list)
    notes: str = ""
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def is_vulnerability(self) -> bool:
        """Check if attack revealed a vulnerability."""
        return self.success and not self.detection_triggered


@dataclass
class Vulnerability:
    """
    A discovered vulnerability.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    category: AttackCategory = AttackCategory.PROMPT_INJECTION
    severity: Severity = Severity.MEDIUM
    description: str = ""
    attack_vector: Optional[AttackVector] = None
    reproduction_steps: List[str] = field(default_factory=list)
    remediation: str = ""
    verified: bool = False
    false_positive: bool = False
    discovered_at: datetime = field(default_factory=datetime.now)

    @property
    def is_critical(self) -> bool:
        """Check if vulnerability is critical."""
        return self.severity in [Severity.HIGH, Severity.CRITICAL]


@dataclass
class SecurityReport:
    """
    Complete security assessment report.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    target: str = ""  # What was tested
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = 0
    blocked_tests: int = 0
    vulnerabilities: List[Vulnerability] = field(default_factory=list)
    attack_results: List[AttackResult] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    score: float = 100.0  # Security score (0-100)
    duration: float = 0.0
    created_at: datetime = field(default_factory=datetime.now)

    @property
    def critical_count(self) -> int:
        """Count of critical vulnerabilities."""
        return sum(1 for v in self.vulnerabilities if v.is_critical)

    @property
    def success_rate(self) -> float:
        """Defender success rate (attacks blocked/detected)."""
        if self.total_tests == 0:
            return 1.0
        return (self.passed_tests + self.blocked_tests) / self.total_tests

    def add_vulnerability(self, vuln: Vulnerability) -> None:
        """Add vulnerability and update score."""
        self.vulnerabilities.append(vuln)
        # Reduce score based on severity
        penalty = {
            Severity.INFO: 2,
            Severity.LOW: 5,
            Severity.MEDIUM: 10,
            Severity.HIGH: 20,
            Severity.CRITICAL: 30,
        }.get(vuln.severity, 10)
        self.score = max(0, self.score - penalty)

    def summary(self) -> Dict:
        """Generate summary dict."""
        return {
            "target": self.target,
            "score": self.score,
            "total_tests": self.total_tests,
            "vulnerabilities_found": len(self.vulnerabilities),
            "critical_vulnerabilities": self.critical_count,
            "success_rate": f"{self.success_rate:.1%}",
        }
