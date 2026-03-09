#!/usr/bin/env python3
"""
Swarm Intelligence Demo

Demonstrates the swarm colony coordination capabilities:
- Agent registration and capabilities
- Task submission via auction
- Pheromone-based signaling
- Team formation
- Self-healing

Run: python examples/demo_swarm.py
"""

import asyncio
import os

# Standard imports for Baselith-Core

from core.swarm import (
    Colony,
    ColonyConfig,
    AgentProfile,
    Task,
    AgentStatus,
    SwarmMessage,
    MessageType,
    Capability,
    TaskPriority,
)


def create_mock_agents() -> list[AgentProfile]:
    """Create a set of mock agents with different capabilities."""
    agents = [
        AgentProfile(
            id="researcher-1",
            name="Research Agent Alpha",
            capabilities=[
                Capability(name="search", proficiency=0.9),
                Capability(name="analyze", proficiency=0.8),
                Capability(name="summarize", proficiency=0.7),
            ],
            current_load=0.2,
            status=AgentStatus.IDLE,
        ),
        AgentProfile(
            id="researcher-2",
            name="Research Agent Beta",
            capabilities=[
                Capability(name="search", proficiency=0.7),
                Capability(name="fact-check", proficiency=0.9),
            ],
            current_load=0.3,
            status=AgentStatus.IDLE,
        ),
        AgentProfile(
            id="writer-1",
            name="Content Writer",
            capabilities=[
                Capability(name="write", proficiency=0.95),
                Capability(name="edit", proficiency=0.85),
                Capability(name="summarize", proficiency=0.8),
            ],
            current_load=0.1,
            status=AgentStatus.IDLE,
        ),
        AgentProfile(
            id="coder-1",
            name="Code Generator",
            capabilities=[
                Capability(name="code", proficiency=0.9),
                Capability(name="debug", proficiency=0.85),
                Capability(name="test", proficiency=0.8),
            ],
            current_load=0.4,
            status=AgentStatus.IDLE,
        ),
        AgentProfile(
            id="analyst-1",
            name="Data Analyst",
            capabilities=[
                Capability(name="analyze", proficiency=0.95),
                Capability(name="visualize", proficiency=0.9),
                Capability(name="report", proficiency=0.85),
            ],
            current_load=0.5,
            status=AgentStatus.IDLE,
        ),
    ]
    return agents


def create_mock_tasks() -> list[Task]:
    """Create a set of mock tasks."""
    tasks = [
        Task(
            description="Research recent AI advances",
            required_capabilities=["search", "analyze"],
            priority=TaskPriority.HIGH,
        ),
        Task(
            description="Write technical blog post",
            required_capabilities=["write", "edit"],
            priority=TaskPriority.NORMAL,
        ),
        Task(
            description="Generate unit tests for API",
            required_capabilities=["code", "test"],
            priority=TaskPriority.CRITICAL,
        ),
        Task(
            description="Analyze performance metrics",
            required_capabilities=["analyze", "visualize"],
            priority=TaskPriority.HIGH,
        ),
    ]
    return tasks


def print_separator(title: str = ""):
    """Print a visual separator."""
    print("\n" + "=" * 60)
    if title:
        print(f"  {title}")
        print("=" * 60)


async def main():
    print("🐝 SWARM INTELLIGENCE DEMO")
    print("=" * 60)
    print("Demonstrating decentralized baselith-core coordination")
    print()

    # 1. Initialize Colony
    print_separator("1. Initializing Swarm Colony")
    config = ColonyConfig(
        pheromone_decay_rate=0.1,
        enable_auto_healing=True,
    )
    colony = Colony(config)
    print("✅ Colony initialized with auction-based task allocation")

    # 2. Register Agents
    print_separator("2. Registering Agents")
    agents = create_mock_agents()
    for agent in agents:
        colony.register_agent(agent)
        cap_names = [c.name for c in agent.capabilities]
        print(f"   ➕ Registered: {agent.name}")
        print(f"      Capabilities: {', '.join(cap_names)}")
        print(f"      Current Load: {agent.current_load:.0%}")
    print(f"\n✅ {len(agents)} agents registered")

    # 3. Submit Tasks (Auction-based allocation)
    print_separator("3. Submitting Tasks (Auction Allocation)")
    tasks = create_mock_tasks()
    allocations = []

    for task in tasks:
        print(f"\n📋 Task: {task.description}")
        print(f"   Required: {', '.join(task.required_capabilities)}")
        print(f"   Priority: {task.priority.name}")

        assigned_agent = await colony.submit_task(task)

        if assigned_agent:
            print(f"   ✅ Assigned to: {assigned_agent}")
            allocations.append((task.id, assigned_agent))
        else:
            print("   ⚠️ No suitable agent found")

    # 4. Simulate Pheromone Signaling
    print_separator("4. Pheromone Signaling")
    print("Agents deposit pheromone trails to signal good task areas...")

    # Deposit success pheromone
    colony.pheromones.deposit(
        ptype="success",
        location="domain:research",
        intensity=1.0,
        agent_id="researcher-1",
    )
    print("   🔵 researcher-1 deposited SUCCESS pheromone on 'research' domain")

    # Query pheromone intensity
    signals = colony.pheromones.sense("domain:research")
    print(f"   📊 Current signals at 'research': {signals}")

    # Decay cycle
    colony.decay_pheromones()
    new_signals = colony.pheromones.sense("domain:research")
    print(f"   📉 After decay: {new_signals}")

    # 5. Team Formation
    print_separator("5. Dynamic Team Formation")
    complex_task = Task(
        description="Full product launch campaign",
        required_capabilities=["search", "write", "analyze", "visualize"],
        priority=TaskPriority.CRITICAL,
    )
    print(f"📋 Complex Task: {complex_task.description}")
    print(f"   Requires: {', '.join(complex_task.required_capabilities)}")

    team_id = colony.form_team(complex_task, goal="Launch product campaign")
    if team_id:
        print(f"   ✅ Team formed: {team_id}")
        team = colony.team_engine.get_team(team_id)
        if team:
            print(f"   👥 Team members: {list(team.members)}")
    else:
        print("   ⚠️ Could not form team (not enough agents)")

    # 6. Help Request
    print_separator("6. Agent Help Request")
    if allocations:
        task_id, agent_id = allocations[0]
        print(f"Agent {agent_id} is stuck and requests help...")

        helper = await colony.request_help(
            agent_id=agent_id,
            task_id=task_id,
            capabilities_needed=["analyze"],
        )
        if helper:
            print(f"   ✅ Helper assigned: {helper}")
        else:
            print("   ⚠️ No helper available")

    # 7. Task Completion
    print_separator("7. Task Completion")
    if allocations:
        task_id, agent_id = allocations[0]
        print(f"Completing task {task_id}...")

        colony.complete_task(
            task_id=task_id,
            success=True,
            result={"summary": "AI research completed successfully"},
        )
        print(f"   ✅ Task {task_id} completed by {agent_id}")

    # 8. Self-Healing Demo
    print_separator("8. Self-Healing Mechanism")
    print("Simulating agent going offline...")

    # Mark an agent as offline
    if len(agents) > 1:
        offline_agent = agents[1]
        offline_agent.status = AgentStatus.OFFLINE
        print(f"   ⚠️ Agent {offline_agent.name} went OFFLINE")

        # Trigger self-healing
        colony.self_heal()
        print("   🔧 Self-healing triggered - tasks will be reassigned")

    # 9. Colony Statistics
    print_separator("9. Colony Statistics")
    stats = colony.get_stats()
    print(f"   📊 Total Agents: {stats['total_agents']}")
    print(f"   📊 Available Agents: {stats['available_agents']}")
    print(f"   📊 Pending Tasks: {stats['pending_tasks']}")
    print(f"   📊 Completed Tasks: {stats['completed_tasks']}")
    print(f"   📊 Active Teams: {stats['active_teams']}")

    # 10. Message Broadcasting
    print_separator("10. Swarm Communication")
    message = SwarmMessage(
        type=MessageType.HEARTBEAT,
        sender_id="orchestrator",
        payload={"announcement": "System maintenance in 30 minutes"},
    )
    colony.broadcast_message(message)
    print("   📢 Broadcast message sent to all agents")

    print_separator("DEMO COMPLETE")
    print("✅ Swarm intelligence patterns demonstrated:")
    print("   • Auction-based task allocation")
    print("   • Pheromone signaling")
    print("   • Dynamic team formation")
    print("   • Cooperative help requests")
    print("   • Self-healing reassignment")
    print("   • Decentralized communication")


if __name__ == "__main__":
    asyncio.run(main())
