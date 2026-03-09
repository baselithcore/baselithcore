#!/usr/bin/env python3
"""
Demo: Multi-Persona Meta-Agent

Demonstrates the Multi-Persona Meta-Agent capability where multiple
personas debate internally to produce balanced, well-reasoned responses.
"""

import asyncio
import logging
from core.observability.logging import get_logger
from core.meta import MultiPersonaAgent, PersonaEnsemble, InternalDebate
from core.personas import Persona

logging.basicConfig(level=logging.INFO)
logger = get_logger(__name__)


async def demo_basic():
    """Basic usage with default configuration."""
    print("\n" + "=" * 60)
    print("DEMO 1: Basic Multi-Persona Reasoning")
    print("=" * 60)

    agent = MultiPersonaAgent()

    query = "Should our startup adopt a microservices architecture?"

    print(f"\nQuery: {query}")
    print(f"Personas: {', '.join(agent.persona_names)}")
    print("\nProcessing...")

    response = await agent.process(query)

    print(f"\n📊 Results:")
    print(f"  - Perspectives generated: {response.perspective_count}")
    print(f"  - Debate rounds: {response.debate_result.total_rounds}")
    print(f"  - Consensus level: {response.debate_result.consensus_level.value}")
    print(f"  - Confidence: {response.confidence:.0%}")

    print(f"\n💬 Final Answer:")
    print(f"  {response.final_answer[:500]}...")

    print(f"\n📝 Synthesis Rationale:")
    print(f"  {response.synthesis_rationale}")


async def demo_custom_personas():
    """Custom personas for domain-specific reasoning."""
    print("\n" + "=" * 60)
    print("DEMO 2: Custom Domain Expert Personas")
    print("=" * 60)

    # Create domain-specific personas
    cto = Persona(
        name="cto",
        description="Chief Technology Officer focused on technical excellence",
        traits={"focus": "scalability", "priority": "technical-debt"},
        temperature=0.6,
    )

    cfo = Persona(
        name="cfo",
        description="Chief Financial Officer focused on cost efficiency",
        traits={"focus": "budget", "priority": "roi"},
        temperature=0.5,
    )

    product_lead = Persona(
        name="product_lead",
        description="Product Lead focused on user experience",
        traits={"focus": "features", "priority": "time-to-market"},
        temperature=0.7,
    )

    ensemble = PersonaEnsemble(personas=[cto, cfo, product_lead])
    agent = MultiPersonaAgent(ensemble=ensemble)

    query = "Should we rebuild our legacy system or continue patching it?"

    print(f"\nQuery: {query}")
    print("Personas: CTO, CFO, Product Lead")
    print("\nProcessing...")

    response = await agent.process(
        query,
        context={
            "system_age": "8 years",
            "tech_debt": "high",
            "upcoming_features": 5,
        },
    )

    print("\n📊 Results:")
    print(f"  - Consensus: {response.debate_result.consensus_level.value}")

    print("\n🗣️ Individual Perspectives:")
    for p in response.perspectives:
        print(f"\n  [{p.persona_name.upper()}]")
        print(f"  {p.content[:200]}...")

    print("\n💬 Synthesized Decision:")
    print(f"  {response.final_answer}")


async def demo_devils_advocate():
    """Demonstrate devil's advocate mode."""
    print("\n" + "=" * 60)
    print("DEMO 3: Devil's Advocate Challenge")
    print("=" * 60)

    # Standard ensemble includes devil's advocate
    agent = MultiPersonaAgent()

    query = "We should definitely use AI for all customer support"

    print(f"\nStatement: {query}")
    print("\nThe devil's advocate will challenge this assertion...")
    print("\nProcessing...")

    response = await agent.process(query)

    # Find the critical perspective
    critical = next(
        (p for p in response.perspectives if p.is_critical),
        None,
    )

    if critical:
        print("\n😈 Devil's Advocate Says:")
        print(f"  {critical.content}")

    print("\n⚖️ Balanced Conclusion:")
    print(f"  {response.final_answer}")

    if response.debate_result.unresolved_tensions:
        print("\n⚠️ Unresolved Tensions:")
        for tension in response.debate_result.unresolved_tensions:
            print(f"  - {tension}")


async def main():
    """Run all demos."""
    print("\n" + "🎭" * 30)
    print("  MULTI-PERSONA META-AGENT DEMO")
    print("🎭" * 30)

    await demo_basic()
    await demo_custom_personas()
    await demo_devils_advocate()

    print("\n" + "=" * 60)
    print("Demo complete!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
