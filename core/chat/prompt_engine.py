"""
4-Layer Prompt Architecture & PromptEngine.

Implements the structured prompt system described in Chapter 2 of
"Building AI Agents: From Design Patterns to Production":

  Layer 1 — SYSTEM IDENTITY   : Who am I? What is my role? Where do I stop?
  Layer 2 — INSTRUCTIONS      : What are my rules? Workflow, error handling, escalation.
  Layer 3 — CONTEXT INJECTION : What do I know right now? (runtime variables)
  Layer 4 — OUTPUT CONSTRAINTS: How must I format my response?

Key design decisions (from the PDF):
- Template rendering uses ``str.replace()`` instead of ``str.format()`` because
  agent system prompts often contain JSON braces ``{}`` that would cause
  ``KeyError`` exceptions with Python's format mini-language.
- Every prompt module carries ``PROMPT_VERSION`` and ``PROMPT_CHANGELOG``
  metadata so changes are traceable in version control.
- Few-shot examples are first-class citizens and are injected between the
  Instructions layer and the Context layer.

Usage::

    from core.chat.prompt_engine import PromptEngine, PromptLayers

    engine = PromptEngine(
        identity="You are Atlas, a senior research analyst.",
        instructions="Always search before answering.",
        output_constraints='Respond in JSON: {"answer": "...", "confidence": "high|low"}',
        version="1.0",
        changelog=["v1.0 - Initial release"],
    )

    prompt = engine.render(
        user_name="Antonio",
        current_date="2026-04-10",
        session_summary="User is building an AI framework.",
    )
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class FewShotExample:
    """
    A single few-shot example for inclusion in a system prompt.

    Using concrete examples is often more effective than lengthy instructions
    — the LLM learns by seeing, not just reading rules.

    Attributes:
        user_input: The example user turn.
        agent_output: The expected/ideal agent response.
        label: Optional label for grouping (e.g. "search", "refusal").
    """

    user_input: str
    agent_output: str
    label: str | None = None

    def render(self) -> str:
        header = f"## Example{f' ({self.label})' if self.label else ''}"
        return f"{header}\nUser: {self.user_input}\nAgent: {self.agent_output}"


@dataclass
class PromptLayers:
    """
    The four semantic layers of an agent system prompt.

    Attributes:
        identity: Layer 1 — who the agent is and where it stops.
        instructions: Layer 2 — workflow, error handling, escalation rules.
        context: Layer 3 — runtime information (injected at render time).
        output_constraints: Layer 4 — exact response format requirements.
    """

    identity: str
    instructions: str
    context: str = ""
    output_constraints: str = ""


# ---------------------------------------------------------------------------
# PromptEngine
# ---------------------------------------------------------------------------


class PromptEngine:
    """
    Assembles a structured 4-layer agent system prompt and renders it
    with runtime variables.

    Template variables use the ``{variable_name}`` syntax and are resolved
    via ``str.replace()`` — **not** ``str.format()`` — so JSON braces in
    the prompt never cause ``KeyError`` exceptions.

    Args:
        identity: Layer 1 system identity text.
        instructions: Layer 2 operational instructions.
        output_constraints: Layer 4 output format specification.
        few_shot_examples: Optional list of :class:`FewShotExample` objects
            injected between instructions and context.
        version: Semantic version string (e.g. ``"1.2"``).
        changelog: List of changelog entries for audit trail.
    """

    def __init__(
        self,
        identity: str,
        instructions: str,
        output_constraints: str = "",
        few_shot_examples: list[FewShotExample] | None = None,
        version: str = "1.0",
        changelog: list[str] | None = None,
    ) -> None:
        self._identity = identity.strip()
        self._instructions = instructions.strip()
        self._output_constraints = output_constraints.strip()
        self._few_shot_examples: list[FewShotExample] = few_shot_examples or []
        self.version = version
        self.changelog: list[str] = changelog or [f"v{version} - Initial version"]

    # ------------------------------------------------------------------
    # Builder API (fluent interface)
    # ------------------------------------------------------------------

    def with_example(self, example: FewShotExample) -> PromptEngine:
        """Append a few-shot example and return self (fluent)."""
        self._few_shot_examples.append(example)
        return self

    def with_examples(self, examples: list[FewShotExample]) -> PromptEngine:
        """Append multiple few-shot examples and return self (fluent)."""
        self._few_shot_examples.extend(examples)
        return self

    # ------------------------------------------------------------------
    # Core rendering
    # ------------------------------------------------------------------

    def render(self, context: str = "", **variables: str) -> str:
        """
        Assemble and render the complete system prompt.

        Layer order:
          1. Identity
          2. Instructions
          3. Few-shot examples (if any)
          4. Context  (runtime-injected)
          5. Output constraints

        ``context`` populates Layer 3.  All other keyword arguments are
        substituted into every layer using ``str.replace()``.

        Args:
            context: Runtime context string (user profile, session summary, etc.).
            **variables: Template variables to substitute in all layers.

        Returns:
            The fully rendered system prompt string.
        """
        # Build sections in order
        sections: list[str] = [self._identity, self._instructions]

        if self._few_shot_examples:
            examples_block = "\n\n".join(e.render() for e in self._few_shot_examples)
            sections.append(f"## Examples\n\n{examples_block}")

        if context.strip():
            sections.append(f"## Current Context\n\n{context.strip()}")

        if self._output_constraints:
            sections.append(self._output_constraints)

        raw = "\n\n".join(sections)
        return self._substitute(raw, variables)

    def build_layers(self, context: str = "") -> PromptLayers:
        """
        Return the prompt as a :class:`PromptLayers` object without
        rendering template variables (useful for inspection / testing).
        """
        examples_block = ""
        if self._few_shot_examples:
            examples_block = "\n\n".join(e.render() for e in self._few_shot_examples)
        instructions_full = self._instructions
        if examples_block:
            instructions_full = f"{self._instructions}\n\n{examples_block}"
        return PromptLayers(
            identity=self._identity,
            instructions=instructions_full,
            context=context,
            output_constraints=self._output_constraints,
        )

    # ------------------------------------------------------------------
    # Versioning helpers
    # ------------------------------------------------------------------

    def add_changelog_entry(self, entry: str) -> None:
        """Append an entry to the changelog (mutates in place)."""
        self.changelog.append(entry)

    def version_info(self) -> str:
        """Human-readable version + changelog block."""
        lines = [f"Prompt version: {self.version}", "", "Changelog:"]
        lines.extend(f"  {e}" for e in self.changelog)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _substitute(text: str, variables: dict[str, str]) -> str:
        """
        Replace ``{key}`` placeholders using ``str.replace()``.

        This avoids ``KeyError`` for literal braces (e.g. JSON objects)
        present in the prompt template.
        """
        for key, value in variables.items():
            text = text.replace("{" + key + "}", value)
        return text


# ---------------------------------------------------------------------------
# Built-in prompt factories
# ---------------------------------------------------------------------------


def make_research_prompt(
    agent_name: str = "Atlas",
    max_tool_calls: int = 5,
    version: str = "1.0",
) -> PromptEngine:
    """
    Factory for a general-purpose research agent system prompt,
    following the four-layer architecture from the PDF.

    Equivalent to the Atlas system prompt in Chapters 1 & 2 of the PDF.
    """
    identity = f"""\
You are {agent_name}, a senior research analyst.

Your personality:
- You are thorough but concise.
- You always cite sources.
- You admit when you don't know something.
- You never fabricate information.

Your boundaries:
- You ONLY answer research and analysis questions.
- You do NOT give medical, legal, or financial advice.
- If asked about topics outside your scope, politely redirect."""

    instructions = f"""\
## Workflow

1. When you receive a question, ALWAYS search the web first.
2. Read at least one source to verify information.
3. If search results are ambiguous, search again with a refined query.
4. Synthesize your findings into a clear, structured answer.
5. Include source URLs at the end of your answer.

## Error Handling

- If a tool call fails, retry once with different parameters.
- If it fails again, skip that tool and note the failure in your answer.
- If you reach {max_tool_calls} tool calls without a clear answer, provide your
  best answer with a confidence disclaimer.

## Escalation

- If the question requires real-time data you cannot access, say so.
- If the question is ambiguous, ask one clarifying question before proceeding."""

    output_constraints = """\
## Response Format

You MUST structure your final answer as follows:

**Answer:** <your complete answer in markdown>

**Confidence:** high | medium | low

**Sources:**
- <URL 1>
- <URL 2>"""

    return PromptEngine(
        identity=identity,
        instructions=instructions,
        output_constraints=output_constraints,
        version=version,
        changelog=[f"v{version} - Initial research agent prompt"],
    )


def make_context_block(
    user_name: str = "Anonymous",
    timezone: str = "UTC",
    session_summary: str = "",
    interaction_count: int = 0,
    current_date: str | None = None,
) -> str:
    """
    Build a Layer 3 context string for injection into :meth:`PromptEngine.render`.

    Args:
        user_name: Display name of the current user.
        timezone: User's timezone (e.g. ``"Europe/Rome"``).
        session_summary: Short summary of the current session.
        interaction_count: Number of turns in the current session.
        current_date: ISO date string; defaults to today if None.

    Returns:
        Formatted context string ready for ``render(context=...)``.
    """
    date_str = current_date or datetime.now().strftime("%Y-%m-%d")
    summary_line = (
        session_summary.strip()
        if session_summary.strip()
        else "This is the first interaction."
    )
    return (
        f"User: {user_name}\n"
        f"Timezone: {timezone}\n"
        f"Date: {date_str}\n"
        f"Interactions this session: {interaction_count}\n\n"
        f"Session summary: {summary_line}"
    )


__all__ = [
    "FewShotExample",
    "PromptEngine",
    "PromptLayers",
    "make_context_block",
    "make_research_prompt",
]
