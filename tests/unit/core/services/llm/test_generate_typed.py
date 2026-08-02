"""Tests for the Pydantic typed-output bridge (generate_typed)."""


class TestGenerateTyped:
    """Pydantic bridge: schema from the model, validated instance out."""

    def _service(self):
        from unittest.mock import AsyncMock, patch

        from core.config.services import LLMConfig
        from core.services.llm.service import LLMService

        config = LLMConfig(provider="ollama", model="llama3.2")
        with patch.object(LLMService, "_create_provider", return_value=AsyncMock()):
            return LLMService(config=config, enable_cache=False)

    async def test_valid_json_returns_model_instance(self):
        from unittest.mock import AsyncMock, patch

        from pydantic import BaseModel

        from core.services.llm.structured import generate_typed
        from core.services.llm.tool_calling import LLMResult

        class Verdict(BaseModel):
            is_real: bool
            confidence: float

        service = self._service()
        with patch(
            "core.services.llm.structured.generate_structured",
            AsyncMock(
                return_value=LLMResult(
                    text='{"is_real": true, "confidence": 0.9}', native=True
                )
            ),
        ) as gen:
            verdict = await generate_typed(service, "judge this", Verdict)
        assert verdict == Verdict(is_real=True, confidence=0.9)
        # The schema was derived from the Pydantic model.
        rf = gen.call_args.kwargs["response_format"]
        assert rf.name == "Verdict"
        assert "is_real" in rf.schema["properties"]

    async def test_fenced_json_is_unwrapped(self):
        from unittest.mock import AsyncMock, patch

        from pydantic import BaseModel

        from core.services.llm.structured import generate_typed
        from core.services.llm.tool_calling import LLMResult

        class Answer(BaseModel):
            value: int

        service = self._service()
        with patch(
            "core.services.llm.structured.generate_structured",
            AsyncMock(
                return_value=LLMResult(text='```json\n{"value": 7}\n```', native=True)
            ),
        ):
            answer = await generate_typed(service, "p", Answer)
        assert answer.value == 7

    async def test_invalid_then_repaired_response_retries(self):
        from unittest.mock import AsyncMock, patch

        from pydantic import BaseModel

        from core.services.llm.structured import generate_typed
        from core.services.llm.tool_calling import LLMResult

        class Answer(BaseModel):
            value: int

        service = self._service()
        responses = [
            LLMResult(text="not json at all", native=True),
            LLMResult(text='{"value": 3}', native=True),
        ]
        with patch(
            "core.services.llm.structured.generate_structured",
            AsyncMock(side_effect=responses),
        ) as gen:
            answer = await generate_typed(service, "p", Answer, retries=1)
        assert answer.value == 3
        # Retry prompt carried the validation error back to the model.
        retry_prompt = gen.call_args_list[1].args[1]
        assert "not valid" in retry_prompt

    async def test_exhausted_retries_raise(self):
        from unittest.mock import AsyncMock, patch

        import pytest as _pytest
        from pydantic import BaseModel

        from core.services.llm.exceptions import LLMProviderError
        from core.services.llm.structured import generate_typed
        from core.services.llm.tool_calling import LLMResult

        class Answer(BaseModel):
            value: int

        service = self._service()
        with patch(
            "core.services.llm.structured.generate_structured",
            AsyncMock(return_value=LLMResult(text='{"wrong": 1}', native=True)),
        ):
            with _pytest.raises(LLMProviderError, match="validation"):
                await generate_typed(service, "p", Answer, retries=1)
