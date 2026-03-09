"""
Prompt Builder.

Provides utilities for building LLM prompts.
Migrated from app/chat/prompt.py
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

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
    additional_context: Optional[str] = None,
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
    history_section = _render_history(history_text)

    # Plugin-provided context (if any)
    plugin_section = ""
    if additional_context:
        plugin_section = f"{additional_context}\n\n"

    return f"""{CONVERSATION_SYSTEM_PROMPT.format(current_date=current_date)}

{history_section}{plugin_section}### CONTEXT:
{context}

### QUESTION:
{user_query}

---

## ANSWER:
"""


__all__ = ["build_prompt", "CONVERSATION_SYSTEM_PROMPT"]
