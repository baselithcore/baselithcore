"""Thinking-mode settings for models served by vLLM.

Kept apart so ``services.py`` stays under the size cap.
:class:`VLLMThinkingSettings` is a base of :class:`~baselith.core.config.services.LLMConfig`:
its fields are LLMConfig fields, read from the environment like the ones declared in
``services.py``; the generated reference lists them under this module's own section.
"""

from __future__ import annotations

from pydantic import AliasChoices, Field

from baselith.core.config._aliased import AliasedSettings


class VLLMThinkingSettings(AliasedSettings):
    """Whether a thinking model served by vLLM reasons before it answers."""

    vllm_enable_thinking: bool | None = Field(
        default=None,
        validation_alias=AliasChoices("LLM_VLLM_ENABLE_THINKING"),
        description=(
            "Whether a thinking model served by vLLM (Qwen3) reasons before it "
            "answers, sent as chat_template_kwargs.enable_thinking on every "
            "request. false is faster and cheaper; true forces it on. Unset "
            "leaves the server's own default. A call that sets enable_thinking "
            "itself keeps its choice."
        ),
    )
