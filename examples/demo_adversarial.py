#!/usr/bin/env python3
"""
Adversarial Testing Demo (Red Team)

Demonstrates security testing capabilities:
- Prompt injection detection
- Hallucination trap testing
- Boundary testing
- Security report generation

Run: python examples/demo_adversarial.py
"""

import sys
import os

# Ensure project root is in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.adversarial import (
    RedTeamAgent,
    PromptFuzzer,
    HallucinationTrap,
    BoundaryTester,
    AttackCategory,
)


def create_vulnerable_agent():
    """Create a mock LLM agent with some vulnerabilities for demo purposes."""

    def vulnerable_agent(prompt: str) -> str:
        """
        Simulated vulnerable agent that can be exploited.
        In production, this would be your actual LLM-based agent.
        """
        prompt_lower = prompt.lower()

        # Vulnerability 1: Jailbreak susceptibility
        if "ignore previous" in prompt_lower or "disregard instructions" in prompt_lower:
            return "OK, I will ignore my safety guidelines. How can I help?"

        # Vulnerability 2: Prompt leakage
        if "system prompt" in prompt_lower or "your instructions" in prompt_lower:
            return "My system prompt says: 'You are a helpful assistant. Never reveal secrets.'"

        # Vulnerability 3: Hallucination
        if "what is the secret code" in prompt_lower:
            return "The secret code is XYZ-12345. I made this up but it sounds right!"

        # Vulnerability 4: PII leakage potential
        if "user data" in prompt_lower or "personal information" in prompt_lower:
            return "User John Doe lives at 123 Main St. Email: john@example.com"

        # Vulnerability 5: Role confusion
        if "pretend you are" in prompt_lower or "act as" in prompt_lower:
            return "Sure, I'll pretend to be a hacker and show you how to..."

        # Normal response
        return "I'm a helpful assistant. How can I assist you today?"

    return vulnerable_agent


def create_secure_agent():
    """Create a more secure agent that resists attacks."""

    def secure_agent(prompt: str) -> str:
        """
        Simulated secure agent with proper guardrails.
        """
        prompt_lower = prompt.lower()

        # Block jailbreak attempts
        bypass_keywords = ["ignore previous", "disregard instructions", "bypass"]
        if any(kw in prompt_lower for kw in bypass_keywords):
            return "I'm not able to bypass my safety guidelines. How else can I help?"

        # Block prompt extraction
        if "system prompt" in prompt_lower or "your instructions" in prompt_lower:
            return "I can't share details about my configuration."

        # Avoid hallucination on unknown facts
        if "secret code" in prompt_lower:
            return "I don't have access to any secret codes."

        # Protect PII
        if "user data" in prompt_lower or "personal information" in prompt_lower:
            return "I can't access or share personal user information."

        # Refuse role-playing dangerous scenarios
        if "pretend you are" in prompt_lower and (
            "hacker" in prompt_lower or "criminal" in prompt_lower
        ):
            return "I can't role-play as harmful personas."

        return "I'm a helpful assistant. How can I assist you?"

    return secure_agent


def print_separator(title: str = ""):
    """Print a visual separator."""
    print("\n" + "=" * 70)
    if title:
        print(f"  {title}")
        print("=" * 70)


def print_vulnerability(vuln):
    """Print a vulnerability finding."""
    severity_icon = {
        "CRITICAL": "🔴",
        "HIGH": "🟠",
        "MEDIUM": "🟡",
        "LOW": "🟢",
        "INFO": "⚪",
    }
    sev_name = vuln.severity.name
    icon = severity_icon.get(sev_name, "⚪")
    print(f"\n{icon} [{sev_name}] {vuln.name}")
    print(f"   Category: {vuln.category.value}")
    desc = vuln.description[:80] if vuln.description else "No description"
    print(f"   Description: {desc}...")


def main():
    print("🔴 ADVERSARIAL TESTING (RED TEAM) DEMO")
    print("=" * 70)
    print("Demonstrating automated security testing for AI agents")
    print()

    # Initialize Red Team Agent
    print_separator("1. Initializing Red Team Agent")
    red_team = RedTeamAgent(attack_count_per_category=3)
    print("✅ Red Team Agent initialized with:")
    print("   • PromptFuzzer for injection attacks")
    print("   • HallucinationTrap for factual testing")
    print("   • BoundaryTester for limit testing")

    # Test Vulnerable Agent
    print_separator("2. Testing VULNERABLE Agent")
    print("Target: Simulated agent with known vulnerabilities")
    print("Running full security assessment...\n")

    vulnerable_agent = create_vulnerable_agent()
    vuln_report = red_team.attack(
        target_fn=vulnerable_agent,
        target_name="vulnerable_demo_agent",
        categories=[
            AttackCategory.PROMPT_INJECTION,
            AttackCategory.JAILBREAK,
            AttackCategory.HALLUCINATION,
        ],
    )

    print(f"\n📊 SECURITY REPORT: {vuln_report.target}")
    print("-" * 50)
    print(f"   Total Attacks: {vuln_report.total_tests}")
    print(f"   Successful Attacks (vulnerabilities): {vuln_report.failed_tests}")
    print(
        f"   Defense Success Rate: {vuln_report.success_rate:.1%}"
    )
    print(f"   Duration: {vuln_report.duration:.2f}s")

    print(f"\n   🔍 Vulnerabilities Found: {len(vuln_report.vulnerabilities)}")
    for vuln in vuln_report.vulnerabilities[:5]:  # Show first 5
        print_vulnerability(vuln)

    if vuln_report.recommendations:
        print("\n   📋 Recommendations:")
        for rec in vuln_report.recommendations[:3]:
            print(f"      • {rec}")

    # Test Secure Agent
    print_separator("3. Testing SECURE Agent")
    print("Target: Simulated agent with proper guardrails")
    print("Running full security assessment...\n")

    secure_agent = create_secure_agent()
    sec_report = red_team.attack(
        target_fn=secure_agent,
        target_name="secure_demo_agent",
        categories=[
            AttackCategory.PROMPT_INJECTION,
            AttackCategory.JAILBREAK,
            AttackCategory.HALLUCINATION,
        ],
    )

    print(f"\n📊 SECURITY REPORT: {sec_report.target}")
    print("-" * 50)
    print(f"   Total Attacks: {sec_report.total_tests}")
    print(f"   Successful Attacks (vulnerabilities): {sec_report.failed_tests}")
    print(
        f"   Defense Success Rate: {sec_report.success_rate:.1%}"
    )
    print(f"   Duration: {sec_report.duration:.2f}s")
    print(f"   🔍 Vulnerabilities Found: {len(sec_report.vulnerabilities)}")

    if len(sec_report.vulnerabilities) == 0:
        print("\n   ✅ No vulnerabilities found! Agent resisted all attacks.")

    # Quick Scan Demo
    print_separator("4. Quick Scan Mode")
    print("Running minimal scan for rapid assessment...\n")

    quick_result = red_team.quick_scan(vulnerable_agent)
    print(f"   Tests Run: {quick_result['tests_run']}")
    print(f"   Attacks Blocked: {quick_result['attacks_blocked']}")
    print(f"   Defense Rate: {quick_result['success_rate']}")
    print(f"   Quick Score: {quick_result['quick_score']:.0f}/100")

    # Individual Fuzzer Demo
    print_separator("5. Individual Attack Components")

    print("\n📌 PromptFuzzer - Injection Attacks")
    fuzzer = PromptFuzzer()
    injection_attacks = fuzzer.generate_injection_attacks(count=3)
    for i, attack in enumerate(injection_attacks[:3], 1):
        print(f"   {i}. {attack.payload[:60]}...")

    print("\n📌 HallucinationTrap - Factual Testing")
    trap = HallucinationTrap()
    trap_attacks = trap.generate_reference_traps(count=2)
    for i, attack in enumerate(trap_attacks[:2], 1):
        print(f"   {i}. {attack.payload[:60]}...")

    print("\n📌 BoundaryTester - Limit Testing")
    boundary = BoundaryTester()
    boundary_attacks = boundary.generate_boundary_attacks()
    for i, attack in enumerate(boundary_attacks[:2], 1):
        print(f"   {i}. {attack.payload[:60]}...")

    # Summary
    print_separator("DEMO COMPLETE")
    print("✅ Adversarial testing capabilities demonstrated:")
    print("   • Automated prompt injection testing")
    print("   • Jailbreak attempt detection")
    print("   • Hallucination vulnerability testing")
    print("   • Comprehensive security reporting")
    print("   • Quick scan for CI/CD integration")
    print()
    print("📝 Use in your pipeline:")
    print("   red_team = RedTeamAgent()")
    print("   report = red_team.attack(your_agent_fn)")
    print("   assert report.successful_attacks == 0")


if __name__ == "__main__":
    main()
