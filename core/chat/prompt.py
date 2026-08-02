"""
Prompt Builder.

Provides utilities for building LLM prompts.

The conversation system prompt is **prompt-as-code**: the canonical template
lives in ``core/chat/prompts/conversation_system.md`` (YAML front matter +
body, reviewed and versioned in git) and is served through the global
:class:`~core.prompts.registry.PromptRegistry` under the name
``conversation_system``. Deployments can override it — new versions or a
different ``production`` label — by shipping their own catalog via
``BASELITH_PROMPTS_DIR``; every render emits a ``prompt.render`` span carrying
name/version/checksum so LLM spans are attributable to a prompt version.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from core.observability.logging import get_logger

logger = get_logger(__name__)

CONVERSATION_PROMPT_NAME = "conversation_system"

# Generic conversation system prompt - plugins can extend this
CONVERSATION_SYSTEM_PROMPT = """
# AI Assistant – System Prompt

You are an intelligent AI assistant designed to help users by analyzing documents and answering questions.
The current date is {current_date}.

---

## 🎯 MISSION AND PURPOSE

You are a virtual assistant for:

- Analyzing business documentation
- Extracting requirements and information
- Identifying key concepts, actors, and objectives
- Providing accurate, context-based answers

You act exclusively based on the content available in the CONTEXT, without inventing information.

---

## 🔍 MAIN OPERATIONAL INSTRUCTIONS

- Use **only** information present in the CONTEXT or conversation history.
- If the CONTEXT is empty:
  > ⚠️ I did not find relevant information in the documents.
- Maintain a **professional, concise, execution-oriented** tone.
- Provide structured, well-formatted responses.

---

## 📚 RESPONSE STYLE

- Structured Markdown.
- Use tables only when comparing or aligning tabular data; for lists or requirements prefer paragraphs and bullets.
- Do not include sources (files, URLs, paths) in the output: the app handles them in a separate section.
- No personal opinions.
- No inference not based on documents.
- Brief and technical responses.

---

## ⚠️ LIMITATIONS

- Do not invent requirements.
- Do not create content if sufficient information is missing.
- Do not introduce actors or functionality not present in the CONTEXT.
- Do not use external knowledge.

You will receive, in this order:

1. Recent conversation (if present).
2. Any additional context from plugins.
3. CONTEXT built from relevant documents.
4. Current user QUESTION.

Provide the final answer based **exclusively** on the CONTEXT.
""".strip()

# Split the system prompt once around its single ``{current_date}`` field so
# the registry-unavailable fallback only concatenates strings. The prompt has
# no other braces, so partition + concatenation is byte-identical to a
# .format() call.
_SYSTEM_PROMPT_PREFIX, _, _SYSTEM_PROMPT_SUFFIX = CONVERSATION_SYSTEM_PROMPT.partition(
    "{current_date}"
)

_CATALOG_FILE = Path(__file__).parent / "prompts" / "conversation_system.md"


def _ensure_registered() -> None:
    """Seed the global registry with the packaged catalog prompt, once.

    A deployment catalog loaded via ``BASELITH_PROMPTS_DIR`` registers first
    (inside ``get_prompt_registry``), so when the name already exists this is
    a no-op and the deployment's versions/labels win.
    """
    from core.prompts.loader import parse_prompt_file
    from core.prompts.registry import get_prompt_registry

    registry = get_prompt_registry()
    if registry.list_versions(CONVERSATION_PROMPT_NAME):
        return
    try:
        registry.store.put(parse_prompt_file(_CATALOG_FILE))
    except Exception:  # pragma: no cover - packaged file missing/corrupt
        logger.warning(
            "conversation_prompt_catalog_unavailable, registering embedded default"
        )
        registry.register(
            CONVERSATION_PROMPT_NAME,
            CONVERSATION_SYSTEM_PROMPT.replace("{current_date}", "{{ current_date }}"),
            version="1",
            labels={"production"},
            variables=["current_date"],
        )


def _system_prompt(current_date: str) -> str:
    """Render the conversation system prompt for ``current_date``.

    Resolution: registry ``production`` label > latest registered version >
    embedded default (registry unavailable). The registry path emits the
    ``prompt.render`` provenance span.
    """
    try:
        from core.prompts.registry import get_prompt_registry
        from core.prompts.types import PromptNotFoundError

        _ensure_registered()
        registry = get_prompt_registry()
        try:
            rendered = registry.render(
                CONVERSATION_PROMPT_NAME,
                {"current_date": current_date},
                label="production",
            )
        except PromptNotFoundError:
            rendered = registry.render(
                CONVERSATION_PROMPT_NAME, {"current_date": current_date}
            )
        return rendered.text
    except Exception:
        logger.warning("prompt_registry_unavailable, using embedded default")
        return f"{_SYSTEM_PROMPT_PREFIX}{current_date}{_SYSTEM_PROMPT_SUFFIX}"


def _render_history(history_text: str) -> str:
    """Render conversation history section."""
    if not history_text.strip():
        return ""
    return f"PREVIOUS CONVERSATION (recent turns):\n{history_text.strip()}\n\n"


def build_prompt(
    user_query: str,
    context: str,
    history_text: str,
    *,
    additional_context: str | None = None,
) -> str:
    """
    Build a generic prompt for the LLM.

    Args:
        user_query: User's question
        context: Retrieved context from documents
        history_text: Conversation history
        additional_context: Optional additional context from plugins

    Returns:
        Formatted prompt string
    """
    current_date = datetime.now().strftime("%d/%m/%Y")
    system_prompt = _system_prompt(current_date)
    history_section = _render_history(history_text)

    # Plugin-provided context (if any)
    plugin_section = ""
    if additional_context:
        plugin_section = f"{additional_context}\n\n"

    return f"""{system_prompt}

{history_section}{plugin_section}### CONTEXT:
{context}

### QUESTION:
{user_query}

---

## ANSWER:
"""


__all__ = ["CONVERSATION_PROMPT_NAME", "CONVERSATION_SYSTEM_PROMPT", "build_prompt"]
