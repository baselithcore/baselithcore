"""
Personas Module

Provides agent persona management:
- Dynamic persona switching
- Personality trait configuration
- Task-indexed few-shot example library (YAML/JSON-backed)
"""

from pathlib import Path

from .defaults import CREATIVE_WRITER, HELPFUL_ASSISTANT, TECHNICAL_EXPERT
from .few_shot import FewShotExample, FewShotLibrary, load_library
from .manager import Persona, PersonaManager

#: Packaged seed library — curated examples shipped with the framework.
DEFAULT_EXAMPLES_PATH = Path(__file__).parent / "examples" / "default_examples.yaml"

__all__ = [
    "CREATIVE_WRITER",
    "DEFAULT_EXAMPLES_PATH",
    "HELPFUL_ASSISTANT",
    "TECHNICAL_EXPERT",
    "FewShotExample",
    "FewShotLibrary",
    "Persona",
    "PersonaManager",
    "load_library",
]
