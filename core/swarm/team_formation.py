"""
Dynamic Multi-Agent Team Assembly.

Engineers specialized teams for complex tasks that exceed the
capabilities of a single agent. Optimizes member selection based on
complementary skill sets and leadership potential.
"""

from core.observability.logging import get_logger
from typing import Dict, List, Optional

from core.config.swarm import TeamConfig
from .types import AgentProfile, TeamFormation, Task

logger = get_logger(__name__)


class TeamFormationEngine:
    """
    Orchestrator for collaborative agent groups.

    Automates the discovery and selection of a 'best-fit' team. Manages
    team lifecycle including formation, leadership assignment, and
    disbandment after goal achievement.
    """

    def __init__(
        self,
        agents: Optional[List[AgentProfile]] = None,
        config: Optional[TeamConfig] = None,
    ):
        """
        Initialize team formation engine.

        Args:
            agents: Available agents
            config: Team formation configuration
        """
        self.config = config or TeamConfig()
        self._agents: Dict[str, AgentProfile] = {}
        self._teams: Dict[str, TeamFormation] = {}

        if agents:
            for agent in agents:
                self.register_agent(agent)

    def register_agent(self, agent: AgentProfile) -> None:
        """Register an agent as available for teams."""
        self._agents[agent.id] = agent

    def unregister_agent(self, agent_id: str) -> None:
        """Remove an agent from availability."""
        self._agents.pop(agent_id, None)

    def form_team(
        self,
        task: Task,
        goal: str = "",
        preferred_agents: Optional[List[str]] = None,
    ) -> Optional[TeamFormation]:
        """
        Form a team for a task.

        Args:
            task: Task requiring a team
            goal: Team goal description
            preferred_agents: Optional list of preferred agent IDs

        Returns:
            Formed team, or None if not possible
        """
        # Find capable agents
        candidates = self._find_candidates(task)

        if len(candidates) < self.config.min_team_size:
            logger.warning(f"Not enough capable agents for task {task.id}")
            return None

        # Prioritize preferred agents
        if preferred_agents:
            preferred = [a for a in candidates if a.id in preferred_agents]
            others = [a for a in candidates if a.id not in preferred_agents]
            candidates = preferred + others

        # Select team members
        members = self._select_members(candidates, task)

        if len(members) < self.config.min_team_size:
            return None

        # Create team
        team = TeamFormation(
            name=f"Team for {task.description[:30]}",
            members={a.id for a in members},
            goal=goal or task.description,
        )

        # Select leader
        team.leader_id = self._select_leader(members)
        team.status = "active"

        self._teams[team.id] = team
        logger.info(f"Team formed: {team.id} with {len(members)} members")

        return team

    def _find_candidates(self, task: Task) -> List[AgentProfile]:
        """Find agents capable of the task."""
        candidates = []

        for agent in self._agents.values():
            if not agent.is_available:
                continue

            score = agent.get_capability_score(task.required_capabilities)
            if score >= self.config.capability_threshold:
                candidates.append(agent)

        return candidates

    def _select_members(
        self,
        candidates: List[AgentProfile],
        task: Task,
    ) -> List[AgentProfile]:
        """Select optimal team members."""
        # Sort by capability match
        scored = []
        for agent in candidates:
            score = agent.get_capability_score(task.required_capabilities)
            load_factor = 1.0 - agent.current_load
            scored.append((agent, score * load_factor))

        scored.sort(key=lambda x: x[1], reverse=True)

        # Select up to max_team_size
        return [agent for agent, _ in scored[: self.config.max_team_size]]

    def _select_leader(self, members: List[AgentProfile]) -> str:
        """Select team leader."""
        if not members:
            return ""

        if self.config.leader_selection == "load":
            # Lowest load
            return min(members, key=lambda a: a.current_load).id
        elif self.config.leader_selection == "random":
            import random

            return random.choice(members).id  # nosec B311
        else:  # capability
            # Highest success rate
            return max(members, key=lambda a: a.success_rate).id

    def disband_team(self, team_id: str) -> bool:
        """Disband a team."""
        if team_id not in self._teams:
            return False

        team = self._teams.pop(team_id)
        team.status = "disbanded"
        logger.info(f"Team disbanded: {team_id}")
        return True

    def get_team(self, team_id: str) -> Optional[TeamFormation]:
        """Get team by ID."""
        return self._teams.get(team_id)

    def get_agent_teams(self, agent_id: str) -> List[TeamFormation]:
        """Get all teams an agent is part of."""
        return [team for team in self._teams.values() if agent_id in team.members]

    def reassign_leader(self, team_id: str) -> Optional[str]:
        """Reassign team leader if current one leaves."""
        team = self._teams.get(team_id)
        if not team or not team.members:
            return None

        # Get member profiles
        members = [self._agents[mid] for mid in team.members if mid in self._agents]

        if not members:
            return None

        team.leader_id = self._select_leader(members)
        return team.leader_id
