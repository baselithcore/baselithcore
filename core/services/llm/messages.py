"""Neutral message types for the message-based agent loop.

The LLM stack historically spoke ``prompt: str -> str``, and the typed agent
loop paid for it: a tool result was stringified into a *rebuilt* user prompt,
which throws away everything a modern provider needs to continue a turn —

* the **correlation** between a result and the call that produced it
  (``tool_use_id``), without which the model cannot tell which of three
  parallel calls answered;
* the **error flag** (``is_error``), without which a failed tool reads as a
  successful one that happened to return the word "Error";
* the assistant turn itself, **verbatim** — including opaque ``thinking``
  blocks, which the API requires to be replayed unchanged;
* the **prompt cache**, because a rebuilt prompt changes the prefix on every
  iteration, so nothing before it can ever be reused.

This module is the import path for the whole surface: the neutral
:class:`Message` / block types (defined in
:mod:`core.services.llm._message_types`) and the provider wire mappings
(:mod:`core.services.llm._message_mapping`). They live in sibling modules for
the file-size cap; nothing else should import those paths directly.

    from core.services.llm.messages import Message, ToolResultBlock, to_anthropic

    history = [Message.user("population of Rome?")]
    history.append(message_from_result(result))            # assistant, verbatim
    history.append(Message.tool_results([ToolResultBlock(
        tool_use_id=call.id, content=observation, is_error=failed,
    )]))
"""

from core.services.llm._message_mapping import (
    CONVERGENCE_NUDGE,
    from_anthropic_content,
    from_openai_message,
    message_from_result,
    render_as_prompt,
    to_anthropic,
    to_openai,
)
from core.services.llm._message_types import (
    ContentBlock,
    ImageBlock,
    Message,
    Role,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)

__all__ = [
    "CONVERGENCE_NUDGE",
    "ContentBlock",
    "ImageBlock",
    "Message",
    "Role",
    "TextBlock",
    "ThinkingBlock",
    "ToolResultBlock",
    "ToolUseBlock",
    "from_anthropic_content",
    "from_openai_message",
    "message_from_result",
    "render_as_prompt",
    "to_anthropic",
    "to_openai",
]
