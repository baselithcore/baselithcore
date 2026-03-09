"""
Meta Module - Multi-Persona Meta-Agent

Provides ensemble reasoning through internal debate between multiple personas.
This module implements a "Society of Mind" pattern where multiple perspectives
are generated, debated, and synthesized into a balanced response.

Key Features:
- Multi-perspective generation from diverse personas
- Structured internal debate with rounds
- Consensus detection and synthesis
- Devil's advocate mode
"""

from .types import Perspective, DebateRound, DebateResult, MetaAgentResponse
from .ensemble import PersonaEnsemble
from .debate import InternalDebate
from .meta_agent import MultiPersonaAgent

__all__ = [
    "Perspective",
    "DebateRound",
    "DebateResult",
    "MetaAgentResponse",
    "PersonaEnsemble",
    "InternalDebate",
    "MultiPersonaAgent",
]
