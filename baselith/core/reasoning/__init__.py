"""
Reasoning Module

Provides advanced reasoning capabilities:
- Chain-of-Thought (CoT)         — explicit step-by-step reasoning trace
- Self-correction loop           — generate → critique → revise
- Tree of Thoughts (ToT)         — explore multiple reasoning branches
- ReAct                          — Reasoning + Acting with Thought/Action/Observation
- Pattern Registry               — choose the right pattern for any task
- Complexity Classifier          — decide whether an agent is even needed
"""

from .cot import ChainOfThought, ReasoningStep
from .patterns import (
    AgentPattern,
    ComplexityAssessment,
    ComplexityClassifier,
    PatternInfo,
    PatternRegistry,
    PatternSelector,
    SelectionResult,
)
from .react import ReActAgent, ReActResult, StepType, ToolDefinition, TraceStep
from .self_correction import SelfCorrector
from .tot import ThoughtNode, TreeOfThoughts, TreeOfThoughtsAsync  # from tot/ package

__all__ = [
    # Chain-of-Thought
    "ChainOfThought",
    "ReasoningStep",
    # Self-correction / Reflection
    "SelfCorrector",
    # Tree of Thoughts
    "TreeOfThoughts",
    "TreeOfThoughtsAsync",
    "ThoughtNode",
    # ReAct
    "ReActAgent",
    "ReActResult",
    "TraceStep",
    "ToolDefinition",
    "StepType",
    # Pattern registry & complexity classifier
    "AgentPattern",
    "PatternInfo",
    "PatternRegistry",
    "PatternSelector",
    "SelectionResult",
    "ComplexityClassifier",
    "ComplexityAssessment",
]
