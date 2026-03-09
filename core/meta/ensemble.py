"""
Persona Ensemble

Manages a collection of personas for multi-perspective generation.
"""

from core.observability.logging import get_logger
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from core.services.llm import LLMService

from core.personas import Persona
from .types import Perspective, DebateRole

logger = get_logger(__name__)


# Default debate personas
ADVOCATE_PERSONA = Persona(
    name="advocate",
    description="Argues constructively in favor of ideas and solutions",
    traits={
        "approach": "supportive",
        "focus": "opportunities",
        "style": "constructive",
    },
    temperature=0.7,
)

CRITIC_PERSONA = Persona(
    name="devils_advocate",
    description="Critically examines ideas to find weaknesses and risks",
    traits={"approach": "skeptical", "focus": "risks", "style": "challenging"},
    temperature=0.6,
)

SYNTHESIZER_PERSONA = Persona(
    name="synthesizer",
    description="Finds common ground and integrates diverse viewpoints",
    traits={"approach": "balanced", "focus": "integration", "style": "diplomatic"},
    temperature=0.5,
)


class PersonaEnsemble:
    """
    Ensemble of personas for multi-perspective reasoning.

    Features:
    - Diverse perspective generation
    - Role-based persona assignment
    - Configurable debate composition
    """

    def __init__(
        self,
        personas: Optional[List[Persona]] = None,
        include_devil_advocate: bool = True,
    ):
        """
        Initialize ensemble.

        Args:
            personas: Custom personas (uses defaults if None)
            include_devil_advocate: Whether to include a critical voice
        """
        if personas:
            self.personas = personas
        else:
            self.personas = [ADVOCATE_PERSONA, SYNTHESIZER_PERSONA]
            if include_devil_advocate:
                self.personas.append(CRITIC_PERSONA)

        self._llm_service: Optional["LLMService"] = None
        self._role_assignments: Dict[str, DebateRole] = {}
        self._assign_roles()

    def _assign_roles(self) -> None:
        """Assign debate roles to personas based on traits."""
        for persona in self.personas:
            if "critic" in persona.name.lower() or "devil" in persona.name.lower():
                self._role_assignments[persona.name] = DebateRole.CRITIC
            elif "synth" in persona.name.lower() or "mediat" in persona.name.lower():
                self._role_assignments[persona.name] = DebateRole.SYNTHESIZER
            else:
                self._role_assignments[persona.name] = DebateRole.ADVOCATE

    @property
    def llm_service(self):
        """Lazy load LLM service."""
        if self._llm_service is None:
            try:
                from core.services.llm import get_llm_service

                self._llm_service = get_llm_service()
            except ImportError:
                logger.warning("LLM service not available")
        return self._llm_service

    def get_role(self, persona_name: str) -> DebateRole:
        """Get role for a persona."""
        return self._role_assignments.get(persona_name, DebateRole.ADVOCATE)

    async def generate_perspectives(
        self,
        query: str,
        context: Optional[Dict] = None,
    ) -> List[Perspective]:
        """
        Generate perspectives from all personas.

        Args:
            query: The question or topic
            context: Optional additional context

        Returns:
            List of perspectives from each persona
        """
        perspectives = []
        ctx_str = str(context) if context else ""

        for persona in self.personas:
            role = self.get_role(persona.name)
            perspective = await self._generate_single_perspective(
                persona, role, query, ctx_str
            )
            perspectives.append(perspective)

        return perspectives

    async def _generate_single_perspective(
        self,
        persona: Persona,
        role: DebateRole,
        query: str,
        context: str,
    ) -> Perspective:
        """Generate a single persona's perspective."""
        prompt = self._build_perspective_prompt(persona, role, query, context)

        content = ""
        reasoning = ""

        if self.llm_service:
            try:
                response = await self.llm_service.generate_response(
                    prompt,
                    temperature=persona.temperature,
                    max_tokens=persona.max_tokens or 1000,
                )
                # Parse response for content and reasoning
                content, reasoning = self._parse_perspective_response(response)
            except Exception as e:
                logger.error(f"Failed to generate perspective for {persona.name}: {e}")
                content = f"[Error generating perspective: {e}]"
        else:
            # Mock response for testing
            content = f"Perspective from {persona.name} on: {query}"
            reasoning = f"Based on {role.value} approach"

        return Perspective(
            persona_name=persona.name,
            role=role,
            content=content,
            reasoning=reasoning,
            confidence=0.8,
            metadata={"temperature": persona.temperature},
        )

    def _build_perspective_prompt(
        self,
        persona: Persona,
        role: DebateRole,
        query: str,
        context: str,
    ) -> str:
        """Build prompt for perspective generation."""
        role_instruction = {
            DebateRole.ADVOCATE: "Focus on opportunities, benefits, and constructive solutions.",
            DebateRole.CRITIC: "Critically examine for weaknesses, risks, and potential problems.",
            DebateRole.MEDIATOR: "Seek balance and consider multiple viewpoints fairly.",
            DebateRole.SYNTHESIZER: "Integrate diverse views into a coherent whole.",
        }

        prefix = persona.get_prompt_prefix()

        return f"""{prefix}

Role in this discussion: {role.value.upper()}
{role_instruction.get(role, "")}

Query: {query}
{"Context: " + context if context else ""}

Provide your perspective with clear reasoning. Structure your response:
PERSPECTIVE: [Your main viewpoint]
REASONING: [Why you hold this view]
CONFIDENCE: [High/Medium/Low]"""

    def _parse_perspective_response(self, response: str) -> tuple:
        """Parse LLM response into content and reasoning."""
        content = response
        reasoning = ""

        if "PERSPECTIVE:" in response:
            parts = response.split("REASONING:")
            if len(parts) >= 2:
                content = parts[0].replace("PERSPECTIVE:", "").strip()
                reasoning = parts[1].split("CONFIDENCE:")[0].strip()
            else:
                content = response.replace("PERSPECTIVE:", "").strip()

        return content, reasoning

    @property
    def persona_names(self) -> List[str]:
        """Get all persona names."""
        return [p.name for p in self.personas]

    def add_persona(self, persona: Persona, role: DebateRole = DebateRole.ADVOCATE):
        """Add a persona with specified role."""
        self.personas.append(persona)
        self._role_assignments[persona.name] = role
