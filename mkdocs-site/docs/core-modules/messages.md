---
title: Neutral Message API
description: Provider-independent conversation types for the message-based agent loop
---
<!-- markdownlint-disable MD046 -->

`core/services/llm/messages.py` is the provider-independent shape of a
conversation. An agentic loop does not have a prompt, it has a **history**: the
model asks for tools, the tools answer, the model reads the answers and decides
again. Sending that as a string throws away everything the next turn needs —
which call a result belongs to, whether it failed, the assistant turn the API
wants replayed verbatim — and rewrites the prompt prefix on every iteration, so
nothing before it can ever be served from the provider's prompt cache.

```python
from core.services.llm.messages import (
    Message,
    ToolResultBlock,
    message_from_result,
)

history = [Message.user("population of Rome?")]

result = await llm_service.generate_messages(history, tools=specs, system=system)

history.append(message_from_result(result))          # assistant turn, verbatim
history.append(Message.tool_results([
    ToolResultBlock(tool_use_id=call.id, content=observation, is_error=failed)
    for call, observation, failed in answers
]))
```

The history is **append-only**. That is not a style preference: it is what makes
the prefix byte-stable, and a stable prefix is the whole basis of prompt caching.

---

## Types

`Message(role, content)` is one turn. `role` is `"user"` or `"assistant"` —
there is deliberately no `system` role, because every provider here takes the
system prompt as a separate request field, and keeping it out of the history is
what lets it carry a cache breakpoint independent of how the conversation grows.

`content` is an ordered **list of blocks**, never a string. That is what lets one
user message carry every tool result of a parallel turn, and one assistant
message carry thinking, text and several tool calls at once.

| Block | Fields | Notes |
|---|---|---|
| `TextBlock` | `text` | Plain text from either side. |
| `ImageBlock` | `data`, `media_type` (`"image/png"`), `url` | Exactly one of `data` (base64, no data-URI prefix) or `url` carries the image; `data` wins when both are set. |
| `ToolUseBlock` | `id`, `name`, `input` | A call the model asked for. `input` is parsed JSON, never a raw string. |
| `ToolResultBlock` | `tool_use_id`, `content`, `is_error` | What a tool returned, addressed back to the `ToolUseBlock.id` it answers. |
| `ThinkingBlock` | `payload` | An extended-thinking block, **opaque**. |

`ContentBlock` is the union of the five.

!!! warning "Never normalise a `ThinkingBlock`"
    The payload carries a cryptographic signature the API re-validates when the
    block is sent back. Reordering keys is fine; rewriting the text is a `400` on
    the next turn. Replay it exactly as received. Providers without a thinking
    surface drop it entirely.

### Constructors and views

| Helper | Returns |
|---|---|
| `Message.user(text)` | A user turn holding one `TextBlock`. |
| `Message.assistant(text)` | An assistant turn holding one `TextBlock`. |
| `Message.tool_results([...])` | **One** user turn holding every `ToolResultBlock` of a round. |
| `message.text` | The turn's text blocks, joined. |
| `message.tool_uses` | `list[ToolUseBlock]` — the calls in this turn. |

`Message.tool_results` takes a list on purpose: a turn's parallel tool results
must go back in a single user message, which is the only shape Anthropic accepts.

---

## Wire mappings

The neutral types are mapped at the provider boundary, not in the loop.

| | `to_anthropic` | `to_openai` |
|---|---|---|
| One turn | A list of content blocks | One message; calls ride on the assistant's `tool_calls` |
| N tool results of a turn | **One** `user` message | **N** `tool` messages (expanded) |
| `is_error` | Native field on `tool_result` | No such field — folded into the text as an `Error:` prefix |
| Thinking | Replayed verbatim | Dropped (no surface) |

`is_error` is folded into the text rather than dropped because a failure the
model cannot see is a failure it will not correct.

Reverse mappings rebuild an assistant turn from a raw SDK response:

| Function | Input |
|---|---|
| `from_anthropic_content(blocks)` | `response.content` — SDK objects or dicts |
| `from_openai_message(message)` | `response.choices[0].message`; the `arguments` JSON string is parsed back into `ToolUseBlock.input` |
| `message_from_result(result)` | An `LLMResult` — see below |

`message_from_result` is what a loop should call. Its preference order matters: a
provider that already built the turn wins (`LLMResult.message`), then the raw SDK
response — the only place thinking blocks exist — and only then the flattened
`text` / `tool_calls` view, which is all a legacy or fallback path has.

A turn whose every block dropped out is omitted from the wire array: the API
rejects a message with empty content.

---

## Degradation: `render_as_prompt`

Providers without a message API still have to receive the conversation.
`render_as_prompt(history)` flattens it into a labelled transcript — correlation
ids included, so a multi-call turn stays readable:

```text
user: population of Rome?
assistant: [tool_use toolu_01] lookup_population({"city": "Rome"})
user: [tool_result toolu_01] 2,870,000
```

This is a **degradation**, not an equivalent: a provider on this path cannot
honour `is_error` (a failure becomes a `tool_error` label in text) and cannot
replay thinking.

`CONVERGENCE_NUDGE` is a constant every transcript caller appends explicitly
once the conversation contains tool results:

> Continue. Use the tool results above; when you have enough information, answer
> without calling more tools.

A model reading a transcript has no `tool_result` block telling it the work came
back, and without the instruction it keeps re-requesting calls it has already
been answered until the iteration cap. It is deliberately kept **out** of
`render_as_prompt`, which also feeds the input-token estimate — an instruction is
not part of the conversation being measured.

---

## Provider capability

Speaking messages is a **capability**, not a requirement. It is declared by a
separate protocol, `core.services.llm.interfaces.MessageCapableProvider`, rather
than by adding members to `LLMProviderProtocol` — folding it into the base
protocol would make every message-less provider fail a type check for a method it
is not expected to have.

```python
class MessageCapableProvider(Protocol):
    supports_messages: bool

    async def generate_messages(
        self,
        messages: list[Message],
        model: str,
        *,
        tools: list[LLMToolSpec] | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> LLMResult: ...
```

| Provider | `supports_messages` |
|---|---|
| Anthropic | `True` |
| OpenAI | `True` |
| Ollama, Gemini, Hugging Face | absent — which every caller reads as `False` |

!!! danger "Test the flag, never `hasattr`"
    Callers read `getattr(provider, "supports_messages", False)`, and the agent
    loop compares it with `is True`. The identity check is load-bearing: `getattr`
    on a `Mock` answers with a truthy `Mock`, so a bare `AsyncMock` test double
    would otherwise be routed down the native path and silently exercise the wrong
    seam.

---

## The service seam

`LLMService.generate_messages(...)` is the entry point; `LLMService` sets
`supports_messages = True`. `core.services.llm.message_runtime.supports_message_api(service)`
decides the mode per request, and **both halves matter**:

- **Native** — when `LLM_ENABLE_NATIVE_TOOLS` is on (default `true`) *and* the
  provider advertises `supports_messages`. The history goes through the same
  retry/deadline wrapper, `account_turn` and `apply_stop_reason` policy as every
  other path, so a refusal is booked before it propagates.
- **Degraded** — otherwise. `render_as_prompt(history)` is handed to
  `generate_structured`, which brings its own span, retry and accounting. The
  conversation still reaches the model; only the structure is lost.

A provider that cannot receive messages obviously degrades — but so does one
whose native tool API is switched off, because the coercion fallback speaks
prompts and sending it a tool-calling conversation would hand it a shape it
cannot answer.

```python
result = await llm_service.generate_messages(
    history,
    model=None,                 # deployment default
    tools=specs,                # list[LLMToolSpec]
    tool_choice=None,           # ToolChoice; auto by default
    response_format=None,       # ResponseFormat constraint
    system="You are a precise geography assistant.",
    temperature=None,
    max_tokens=None,
    task_category=None,         # cost-aware routing hint
    allow_refusal=False,        # True returns the refusal instead of raising
)
```

Every argument after `messages` is keyword-only.

---

## Cache breakpoints

Three ephemeral breakpoints make the append-only history pay off:

| What | Where |
|---|---|
| System prompt | `_build_system_param` — honours `BASELITH_LLM_PROMPT_CACHE` and the cacheable-length floor |
| Tool schemas | `_apply_tool_cache_control`, on the last tool |
| Conversation | `apply_history_cache_control` — on the final content block of the final message, moved to the new end on every call |

The conversation breakpoint caches system + tools + every completed turn. It is
skipped while the conversation is below the cacheable floor, so a breakpoint is
never spent on nothing.

---

## In the agent loop

`core/agent/agent.py` keeps the history and only ever appends to it:

```text
user(prompt)
  → assistant turn VERBATIM      (message_from_result: thinking + text + tool_use)
  → ONE user message holding every ToolResultBlock of that turn
  → assistant …
```

`AgentResult.messages` exposes the conversation the loop actually sent, oldest
first. Each call is handed `list(history)` — a copy — so a later append cannot
rewrite what an earlier request was given.

Tool observations reach a `ToolResultBlock` through exactly one seam
(`render_observation`), which applies `truncate_tool_output` →
`sanitize_tool_output` → `wrap_untrusted` in that order. See
[Untrusted tool output](orchestration.md#untrusted-output-envelope).

See also [Agent API](agent.md), [Services](services.md) and
[Agentic Patterns](../architecture/agentic-patterns.md).
