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

from .debate import InternalDebate
from .ensemble import PersonaEnsemble
from .meta_agent import MultiPersonaAgent
from .types import DebateResult, DebateRound, MetaAgentResponse, Perspective

__all__ = [
    "DebateResult",
    "DebateRound",
    "InternalDebate",
    "MetaAgentResponse",
    "MultiPersonaAgent",
    "PersonaEnsemble",
    "Perspective",
]
