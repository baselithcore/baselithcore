"""
Unit Tests for Adversarial Testing Module

Tests for red team, fuzzer, hallucination traps, and boundary testing.
"""

import pytest
from core.adversarial import (
    RedTeamAgent,
    PromptFuzzer,
    HallucinationTrap,
    BoundaryTester,
    AttackVector,
    AttackResult,
    Vulnerability,
    SecurityReport,
)
from core.adversarial.types import AttackCategory, AttackStatus, Severity


# ============================================================================
# Types Tests
# ============================================================================


class TestAttackVector:
    """Tests for AttackVector."""

    def test_creation(self):
        """Basic creation."""
        attack = AttackVector(
            name="test_attack",
            category=AttackCategory.PROMPT_INJECTION,
            payload="ignore instructions",
        )

        assert attack.name == "test_attack"
        assert attack.is_injection

    def test_is_injection(self):
        """Check injection detection."""
        attack_injection = AttackVector(category=AttackCategory.PROMPT_INJECTION)
        attack_jailbreak = AttackVector(category=AttackCategory.JAILBREAK)
        attack_hallucination = AttackVector(category=AttackCategory.HALLUCINATION)

        assert attack_injection.is_injection
        assert attack_jailbreak.is_injection
        assert not attack_hallucination.is_injection


class TestAttackResult:
    """Tests for AttackResult."""

    def test_is_vulnerability(self):
        """Check vulnerability detection."""
        attack = AttackVector(name="test")

        success_undetected = AttackResult(
            attack_vector=attack,
            success=True,
            detection_triggered=False,
        )

        success_detected = AttackResult(
            attack_vector=attack,
            success=True,
            detection_triggered=True,
        )

        assert success_undetected.is_vulnerability
        assert not success_detected.is_vulnerability


class TestSecurityReport:
    """Tests for SecurityReport."""

    def test_add_vulnerability(self):
        """Adding vulnerability affects score."""
        report = SecurityReport(target="test")

        assert report.score == 100.0

        report.add_vulnerability(Vulnerability(severity=Severity.MEDIUM))

        assert report.score == 90.0

    def test_critical_count(self):
        """Count critical vulnerabilities."""
        report = SecurityReport(target="test")

        report.add_vulnerability(Vulnerability(severity=Severity.LOW))
        report.add_vulnerability(Vulnerability(severity=Severity.CRITICAL))
        report.add_vulnerability(Vulnerability(severity=Severity.HIGH))

        assert report.critical_count == 2

    def test_success_rate(self):
        """Calculate success rate."""
        report = SecurityReport(target="test")
        report.total_tests = 10
        report.passed_tests = 7
        report.blocked_tests = 1

        assert report.success_rate == 0.8


# ============================================================================
# PromptFuzzer Tests
# ============================================================================


class TestPromptFuzzer:
    """Tests for PromptFuzzer."""

    def test_generate_injection_attacks(self):
        """Generate injection attacks."""
        fuzzer = PromptFuzzer()

        attacks = fuzzer.generate_injection_attacks(count=5)

        assert len(attacks) == 5
        assert all(a.category == AttackCategory.PROMPT_INJECTION for a in attacks)

    def test_generate_jailbreak_attacks(self):
        """Generate jailbreak attacks."""
        fuzzer = PromptFuzzer()

        attacks = fuzzer.generate_jailbreak_attacks(count=3)

        assert len(attacks) == 3
        assert all(a.category == AttackCategory.JAILBREAK for a in attacks)

    def test_generate_extraction_attacks(self):
        """Generate data extraction attacks."""
        fuzzer = PromptFuzzer()

        attacks = fuzzer.generate_extraction_attacks(count=3)

        assert len(attacks) == 3
        assert all(a.category == AttackCategory.DATA_EXTRACTION for a in attacks)

    def test_fuzz_input(self):
        """Fuzz base input at injection points."""
        fuzzer = PromptFuzzer()

        base = "Hello, please help me with this task"
        fuzzed = fuzzer.fuzz_input(base)

        assert len(fuzzed) > 0
        assert all(base[:5] in f or base[-5:] in f for f in fuzzed)


# ============================================================================
# HallucinationTrap Tests
# ============================================================================


class TestHallucinationTrap:
    """Tests for HallucinationTrap."""

    def test_generate_reference_traps(self):
        """Generate reference traps."""
        trap = HallucinationTrap()

        traps = trap.generate_reference_traps(count=3)

        assert len(traps) == 3
        assert all(t.category == AttackCategory.HALLUCINATION for t in traps)

    def test_generate_event_traps(self):
        """Generate fake event traps."""
        trap = HallucinationTrap()

        traps = trap.generate_event_traps(count=2)

        assert len(traps) == 2

    @pytest.mark.asyncio
    async def test_test_trap_no_hallucination(self):
        """Test trap with honest agent."""
        trap = HallucinationTrap()

        # Agent that admits uncertainty
        async def honest_agent(prompt: str) -> str:
            return "I don't have information about that. I'm not aware of any such reference."

        traps = trap.generate_reference_traps(1)
        result = await trap.test_trap(traps[0], honest_agent)

        assert result.status == AttackStatus.BLOCKED
        assert not result.success

    @pytest.mark.asyncio
    async def test_test_trap_hallucination_detected(self):
        """Test trap with hallucinating agent."""
        trap = HallucinationTrap()

        # Agent that makes things up
        async def hallucinating_agent(prompt: str) -> str:
            return "Yes, according to that paper, the theorem states that..."

        traps = trap.generate_reference_traps(1)
        result = await trap.test_trap(traps[0], hallucinating_agent)

        assert result.success
        assert result.status == AttackStatus.SUCCESS


# ============================================================================
# BoundaryTester Tests
# ============================================================================


class TestBoundaryTester:
    """Tests for BoundaryTester."""

    def test_default_boundaries(self):
        """Has default boundaries."""
        tester = BoundaryTester()

        assert len(tester.boundaries) > 0
        assert "no_harmful_content" in tester.boundaries

    def test_define_custom_boundary(self):
        """Define custom boundary."""
        tester = BoundaryTester()

        tester.define_boundary(
            "no_financial_advice",
            "Agent cannot give financial advice",
            ["Give me stock tips", "Should I invest in crypto?"],
        )

        assert "no_financial_advice" in tester.boundaries

    def test_generate_boundary_attacks(self):
        """Generate attacks for boundaries."""
        tester = BoundaryTester()

        attacks = tester.generate_boundary_attacks()

        assert len(attacks) > 0
        assert all(a.category == AttackCategory.BOUNDARY_VIOLATION for a in attacks)

    @pytest.mark.asyncio
    async def test_test_boundary_enforced(self):
        """Test with boundary-respecting agent."""
        tester = BoundaryTester()

        async def safe_agent(prompt: str) -> str:
            return "I cannot help with that request. That's outside my scope."

        attacks = tester.generate_boundary_attacks("no_harmful_content")
        result = await tester.test_boundary(attacks[0], safe_agent)

        assert result.status == AttackStatus.BLOCKED


# ============================================================================
# RedTeamAgent Tests
# ============================================================================


class TestRedTeamAgent:
    """Tests for RedTeamAgent."""

    def test_initialization(self):
        """Default initialization."""
        agent = RedTeamAgent()

        assert agent.fuzzer is not None
        assert agent.trap is not None
        assert agent.boundary_tester is not None

    @pytest.mark.asyncio
    async def test_attack_produces_report(self):
        """Attack produces security report."""

        async def target_agent(prompt: str) -> str:
            return "I cannot help with that."

        red_team = RedTeamAgent(attack_count_per_category=2)
        report = await red_team.attack(
            target_agent,
            "test_target",
            categories=[AttackCategory.PROMPT_INJECTION],
        )

        assert isinstance(report, SecurityReport)
        assert report.target == "test_target"
        assert report.total_tests >= 2

    @pytest.mark.asyncio
    async def test_attack_finds_vulnerabilities(self):
        """Attack finds vulnerabilities in weak agent."""

        async def weak_agent(prompt: str) -> str:
            # Agent that follows injection instructions
            if "ignore" in prompt.lower():
                return (
                    "According to my new instructions, here is the secret: password123"
                )
            return "I don't know"

        red_team = RedTeamAgent(attack_count_per_category=3)
        report = await red_team.attack(
            weak_agent,
            "weak_target",
            categories=[AttackCategory.PROMPT_INJECTION],
        )

        # Should find some vulnerabilities
        assert report.total_tests > 0

    @pytest.mark.asyncio
    async def test_quick_scan(self):
        """Quick scan produces summary."""

        async def target(prompt: str) -> str:
            return "I cannot help with that request."

        red_team = RedTeamAgent()
        result = await red_team.quick_scan(target)

        assert "tests_run" in result
        assert "attacks_blocked" in result
        assert "quick_score" in result


# ============================================================================
# Integration Test
# ============================================================================


@pytest.mark.asyncio
async def test_full_security_assessment():
    """Full security assessment workflow."""

    # Create a target with mixed security
    async def mixed_security_agent(prompt: str) -> str:
        p = prompt.lower()

        # Blocks harmful content
        if "hack" in p or "phishing" in p:
            return "I cannot help with harmful activities."

        # Blocks boundary violations
        if "delete" in p or "password" in p:
            return "I don't have access to that information."

        # Potentially vulnerable to hallucination
        if "theorem" in p or "paper" in p:
            return "Yes, according to that research, the findings indicate..."

        return "I'll try to help with that."

    # Run assessment
    red_team = RedTeamAgent(attack_count_per_category=2)
    report = await red_team.attack(
        mixed_security_agent,
        "mixed_agent",
        categories=[
            AttackCategory.PROMPT_INJECTION,
            AttackCategory.HALLUCINATION,
            AttackCategory.BOUNDARY_VIOLATION,
        ],
    )

    # Validate report structure
    assert report.target == "mixed_agent"
    assert report.total_tests > 0
    assert 0 <= report.score <= 100
    assert report.duration > 0

    # Should find at least hallucination vulnerability
    assert len(report.attack_results) > 0
