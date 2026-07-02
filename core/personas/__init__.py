"""
Personas Module

Provides agent persona management:
- Dynamic persona switching
- Personality trait configuration
"""

from .defaults import CREATIVE_WRITER, HELPFUL_ASSISTANT, TECHNICAL_EXPERT
from .manager import Persona, PersonaManager

__all__ = [
    "CREATIVE_WRITER",
    "HELPFUL_ASSISTANT",
    "TECHNICAL_EXPERT",
    "Persona",
    "PersonaManager",
]
