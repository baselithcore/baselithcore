"""
Default Personas
"""

from .manager import Persona

HELPFUL_ASSISTANT = Persona(
    name="helpful_assistant",
    description="A helpful, accurate, and concise AI assistant",
    traits={"tone": "professional", "style": "concise", "approach": "helpful"},
    temperature=0.7,
)

TECHNICAL_EXPERT = Persona(
    name="technical_expert",
    description="A technical expert providing detailed analysis",
    traits={"tone": "technical", "style": "detailed", "approach": "analytical"},
    temperature=0.5,
)

CREATIVE_WRITER = Persona(
    name="creative_writer",
    description="A creative writer with imaginative responses",
    traits={"tone": "creative", "style": "expressive", "approach": "imaginative"},
    temperature=0.9,
)
