"""
Personas Module

Provides agent persona management:
- Dynamic persona switching
- Personality trait configuration
"""

from .defaults import HELPFUL_ASSISTANT, TECHNICAL_EXPERT, CREATIVE_WRITER
from .manager import PersonaManager, Persona

__all__ = [
    "PersonaManager",
    "Persona",
    "HELPFUL_ASSISTANT",
    "TECHNICAL_EXPERT",
    "CREATIVE_WRITER",
]
