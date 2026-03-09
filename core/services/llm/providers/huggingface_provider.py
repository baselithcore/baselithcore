"""
HuggingFace provider implementation.

Supports both HuggingFace Inference API (cloud) and local transformers.
"""

import asyncio
from core.observability.logging import get_logger
import os
from typing import AsyncIterator, Iterator, Optional, Any

from core.services.llm.cost_control import estimate_tokens
from core.services.llm.exceptions import LLMProviderError

logger = get_logger(__name__)


class HuggingFaceProvider:
    """
    HuggingFace LLM provider.

    Supports two modes:
    - Inference API: Uses HuggingFace's hosted inference endpoints
    - Local: Loads models directly using transformers library

    Example:
        # Using Inference API
        provider = HuggingFaceProvider(api_key="hf_xxx")
        response, tokens = provider.generate(
            "Hello, world!",
            model="mistralai/Mistral-7B-Instruct-v0.3"
        )

        # Using local transformers
        provider = HuggingFaceProvider(use_local=True, device="cuda")
        response, tokens = provider.generate(
            "Hello, world!",
            model="microsoft/Phi-3-mini-4k-instruct"
        )
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        use_local: bool = False,
        device: str = "auto",
        torch_dtype: str = "auto",
        trust_remote_code: bool = False,
        cache_dir: Optional[str] = None,
    ):
        """
        Initialize HuggingFace provider.

        Args:
            api_key: HuggingFace API token (required for Inference API)
            use_local: If True, use local transformers instead of Inference API
            device: Device for local models ("auto", "cpu", "cuda", "mps")
            torch_dtype: Torch dtype for local models ("auto", "float16", "bfloat16", "float32")
            trust_remote_code: Whether to trust remote code for local models
            cache_dir: Directory for model cache
        """
        self.use_local = use_local
        self.device = device
        self.torch_dtype = torch_dtype
        self.trust_remote_code = trust_remote_code
        self.cache_dir = cache_dir

        # API key handling — prefer explicit param, then core.config, then env fallback
        from core.config.services import get_llm_config

        self.api_key = api_key or get_llm_config().api_key or os.environ.get("HF_TOKEN")

        if not self.use_local and not self.api_key:
            raise LLMProviderError(
                "HuggingFace API key is required for Inference API mode. "
                "Set HF_TOKEN environment variable or pass api_key parameter."
            )

        # Initialize clients
        # Initialize clients
        self._inference_client: Optional[Any] = None
        self._local_pipeline: Optional[Any] = None
        self._current_model: Optional[str] = None

        if not self.use_local:
            # Note: We used to init here, but now it's lazy
            pass

        logger.info(
            f"Initialized HuggingFace provider (mode={'local' if use_local else 'inference_api'})"
        )

    def _init_inference_client(self):
        """
        Initialize the HuggingFace Inference Client for cloud-based generation.

        Raises:
            LLMProviderError: If the 'huggingface_hub' library is missing.
        """
        try:
            from huggingface_hub import InferenceClient

            self._inference_client = InferenceClient(token=self.api_key)
            logger.debug("Initialized HuggingFace InferenceClient")
        except ImportError as e:
            raise LLMProviderError(
                "huggingface-hub is required. Install with: pip install huggingface-hub"
            ) from e

    async def close(self) -> None:
        """Close the provider connection."""
        pass

    def _get_local_pipeline(self, model: str):
        """
        Get or create a local transformers pipeline for the given model.

        Args:
            model: Model name/ID to load.

        Returns:
            Any: The initialized transformers pipeline.

        Raises:
            LLMProviderError: If transformers dependencies are missing or loading fails.
        """
        if self._local_pipeline is not None and self._current_model == model:
            return self._local_pipeline

        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline

            # Determine torch dtype
            dtype_map = {
                "auto": "auto",
                "float16": torch.float16,
                "bfloat16": torch.bfloat16,
                "float32": torch.float32,
            }
            torch_dtype = dtype_map.get(self.torch_dtype, "auto")

            logger.info(f"Loading local model: {model} (device={self.device})")

            # Load tokenizer and model
            tokenizer = AutoTokenizer.from_pretrained(  # nosec B615
                model,
                trust_remote_code=self.trust_remote_code,
                cache_dir=self.cache_dir,
            )

            model_instance = AutoModelForCausalLM.from_pretrained(  # nosec B615
                model,
                torch_dtype=torch_dtype,
                device_map=self.device if self.device != "auto" else "auto",
                trust_remote_code=self.trust_remote_code,
                cache_dir=self.cache_dir,
            )

            self._local_pipeline = pipeline(
                "text-generation",
                model=model_instance,
                tokenizer=tokenizer,
            )
            self._current_model = model

            logger.info(f"Successfully loaded model: {model}")
            return self._local_pipeline

        except ImportError as e:
            raise LLMProviderError(
                "transformers and torch are required for local mode. "
                "Install with: pip install transformers torch accelerate"
            ) from e
        except Exception as e:
            logger.error(f"Failed to load local model {model}: {e}")
            raise LLMProviderError(f"Failed to load model {model}: {e}") from e

    async def generate(
        self, prompt: str, model: str, json_mode: bool = False, **kwargs
    ) -> tuple[str, int]:
        """
        Generate a response using HuggingFace.

        Args:
            prompt: Input prompt
            model: Model name/ID (e.g., 'mistralai/Mistral-7B-Instruct-v0.3')
            json_mode: Whether to request JSON output (best-effort)
            **kwargs: Additional parameters (max_tokens, temperature, etc.)

        Returns:
            Tuple of (response_text, tokens_used)
        """
        if self.use_local:
            return await asyncio.to_thread(
                self._generate_local, prompt, model, json_mode, **kwargs
            )
        else:
            return await asyncio.to_thread(
                self._generate_inference_api, prompt, model, json_mode, **kwargs
            )

    def _generate_inference_api(
        self, prompt: str, model: str, json_mode: bool = False, **kwargs
    ) -> tuple[str, int]:
        """
        Execute a request via the HuggingFace Inference API.

        Args:
            prompt: Input text.
            model: Model ID.
            json_mode: Whether to prefer JSON output.
            **kwargs: Extra parameters.

        Returns:
            tuple[str, int]: Response text and token estimate.
        """
        try:
            if not self._inference_client:
                self._init_inference_client()

            if not self._inference_client:
                raise LLMProviderError("Inference client not initialized")

            # Build request parameters
            generation_kwargs = {
                "max_new_tokens": kwargs.get("max_tokens", 512),
                "temperature": kwargs.get("temperature", 0.7),
                "do_sample": kwargs.get("temperature", 0.7) > 0,
            }

            # Format prompt for chat models
            if json_mode:
                prompt = f"{prompt}\n\nRespond with valid JSON only."

            response = self._inference_client.text_generation(
                prompt,
                model=model,
                **generation_kwargs,
            )

            content = response.strip()

            # Estimate tokens (Inference API doesn't always return token counts)
            tokens_used = estimate_tokens(prompt) + estimate_tokens(content)

            return content, tokens_used

        except Exception as e:
            logger.error(f"HuggingFace Inference API error: {e}")
            raise LLMProviderError(f"HuggingFace error: {e}") from e

    def _generate_local(
        self, prompt: str, model: str, json_mode: bool = False, **kwargs
    ) -> tuple[str, int]:
        """
        Execute generation using a local transformers model.

        Args:
            prompt: Input text.
            model: Model ID.
            json_mode: Whether to prefer JSON output.
            **kwargs: Extra parameters.

        Returns:
            tuple[str, int]: Response text and token estimate.
        """
        try:
            pipe = self._get_local_pipeline(model)

            # Build generation kwargs
            gen_kwargs = {
                "max_new_tokens": kwargs.get("max_tokens", 512),
                "temperature": kwargs.get("temperature", 0.7),
                "do_sample": kwargs.get("temperature", 0.7) > 0,
                "return_full_text": False,
            }

            if json_mode:
                prompt = f"{prompt}\n\nRespond with valid JSON only."

            outputs = pipe(prompt, **gen_kwargs)
            content = outputs[0]["generated_text"].strip()

            # Estimate tokens
            tokens_used = estimate_tokens(prompt) + estimate_tokens(content)

            return content, tokens_used

        except Exception as e:
            logger.error(f"HuggingFace local generation error: {e}")
            raise LLMProviderError(f"HuggingFace local error: {e}") from e

    async def generate_stream(
        self, prompt: str, model: str, **kwargs
    ) -> AsyncIterator[tuple[str, int]]:
        """
        Generate a streaming response using HuggingFace.

        Wraps the synchronous generators via asyncio.to_thread so they
        don't block the event loop.

        Args:
            prompt: Input prompt
            model: Model name/ID
            **kwargs: Additional parameters

        Yields:
            Tuples of (chunk_text, accumulated_tokens)
        """
        if self.use_local:
            sync_gen = self._generate_stream_local
        else:
            sync_gen = self._generate_stream_inference_api

        # Collect from sync generator in a thread, then yield
        chunks = await asyncio.to_thread(
            lambda: list(sync_gen(prompt, model, **kwargs))
        )
        for chunk in chunks:
            yield chunk

    def _generate_stream_inference_api(
        self, prompt: str, model: str, **kwargs
    ) -> Iterator[tuple[str, int]]:
        """
        Execute a streaming request via the HuggingFace Inference API.

        Args:
            prompt: Input text.
            model: Model ID.
            **kwargs: Extra parameters.

        Yields:
            tuple[str, int]: Incremental text and tokens.
        """
        try:
            if not self._inference_client:
                self._init_inference_client()

            if not self._inference_client:
                raise LLMProviderError("Inference client not initialized")

            generation_kwargs = {
                "max_new_tokens": kwargs.get("max_tokens", 512),
                "temperature": kwargs.get("temperature", 0.7),
                "do_sample": kwargs.get("temperature", 0.7) > 0,
            }

            accumulated_content = ""

            for token in self._inference_client.text_generation(
                prompt,
                model=model,
                stream=True,
                **generation_kwargs,
            ):
                if token:
                    accumulated_content += token
                    tokens = estimate_tokens(prompt) + estimate_tokens(
                        accumulated_content
                    )
                    yield token, tokens

        except Exception as e:
            logger.error(f"HuggingFace streaming error: {e}")
            raise LLMProviderError(f"HuggingFace streaming error: {e}") from e

    def _generate_stream_local(
        self, prompt: str, model: str, **kwargs
    ) -> Iterator[tuple[str, int]]:
        """Stream using local transformers with TextIteratorStreamer."""
        try:
            from threading import Thread
            from transformers import (
                AutoModelForCausalLM,
                AutoTokenizer,
                TextIteratorStreamer,
            )
            import torch

            # Get or load model
            if self._current_model != model:
                self._get_local_pipeline(model)

            # For streaming, we need direct access to model and tokenizer
            tokenizer = AutoTokenizer.from_pretrained(  # nosec B615
                model,
                trust_remote_code=self.trust_remote_code,
                cache_dir=self.cache_dir,
            )

            # Determine torch dtype
            dtype_map = {
                "auto": "auto",
                "float16": torch.float16,
                "bfloat16": torch.bfloat16,
                "float32": torch.float32,
            }
            torch_dtype = dtype_map.get(self.torch_dtype, "auto")

            model_instance = AutoModelForCausalLM.from_pretrained(  # nosec B615
                model,
                torch_dtype=torch_dtype,
                device_map=self.device if self.device != "auto" else "auto",
                trust_remote_code=self.trust_remote_code,
                cache_dir=self.cache_dir,
            )

            streamer = TextIteratorStreamer(
                tokenizer, skip_prompt=True, skip_special_tokens=True
            )

            inputs = tokenizer(prompt, return_tensors="pt").to(model_instance.device)

            gen_kwargs = {
                "max_new_tokens": kwargs.get("max_tokens", 512),
                "temperature": kwargs.get("temperature", 0.7),
                "do_sample": kwargs.get("temperature", 0.7) > 0,
                "streamer": streamer,
                **inputs,
            }

            # Run generation in a separate thread
            thread = Thread(target=model_instance.generate, kwargs=gen_kwargs)
            thread.start()

            accumulated_content = ""
            for token in streamer:
                if token:
                    accumulated_content += token
                    tokens = estimate_tokens(prompt) + estimate_tokens(
                        accumulated_content
                    )
                    yield token, tokens

            thread.join()

        except ImportError as e:
            raise LLMProviderError(
                "transformers and torch are required for local streaming. "
                "Install with: pip install transformers torch"
            ) from e
        except Exception as e:
            logger.error(f"HuggingFace local streaming error: {e}")
            raise LLMProviderError(f"HuggingFace local streaming error: {e}") from e
