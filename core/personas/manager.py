"""
Agent Persona and Behavioral Profile Management.

Coordinates the psychological and behavioral blueprints for autonomous
entities. Manages the injection of tone, expertise, and constraints
into agent prompts, ensuring consistent identity and adherence to
domain-specific interaction styles.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Persona:
    """Agent persona definition."""

    name: str
    description: str
    traits: Dict[str, str] = field(default_factory=dict)
    system_prompt: str = ""
    temperature: float = 0.7
    max_tokens: int = 1000

    def get_prompt_prefix(self) -> str:
        """Get persona prompt prefix."""
        if self.system_prompt:
            return self.system_prompt

        traits_str = ", ".join(f"{k}: {v}" for k, v in self.traits.items())
        return f"You are {self.name}. {self.description}. Traits: {traits_str}"


class PersonaManager:
    """
    Manages multiple agent personas.

    Features:
    - Persona registration
    - Dynamic switching
    - Default persona
    """

    def __init__(self, default_persona: Optional[Persona] = None):
        """Initialize with optional default persona."""
        self._personas: Dict[str, Persona] = {}
        self._active: Optional[str] = None

        if default_persona:
            self._personas[default_persona.name] = default_persona
            self._active = default_persona.name

    async def register(self, persona: Persona) -> None:
        """Register a persona."""
        self._personas[persona.name] = persona

    async def switch(self, name: str) -> bool:
        """Switch to a persona. Returns False if not found."""
        if name in self._personas:
            self._active = name
            return True
        return False

    async def get_active(self) -> Optional[Persona]:
        """Get currently active persona."""
        if self._active:
            return self._personas.get(self._active)
        return None

    async def get(self, name: str) -> Optional[Persona]:
        """Get persona by name."""
        return self._personas.get(name)

    async def list_all(self) -> List[str]:
        """List all registered persona names."""
        return list(self._personas.keys())
