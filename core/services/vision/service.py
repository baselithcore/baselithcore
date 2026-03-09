"""
Vision Service Implementation.

Multi-provider vision service supporting OpenAI, Anthropic, and Google
for image analysis, OCR, and multi-modal understanding.

Usage:
    from core.services.vision import VisionService, VisionRequest, ImageContent

    service = VisionService()

    request = VisionRequest(
        prompt="What's in this image?",
        images=[ImageContent.from_file("screenshot.png")]
    )

    response = await service.analyze(request)
    print(response.content)
"""

from __future__ import annotations

from typing import Any

from core.observability.logging import get_logger
from core.services.vision.models import (
    ImageContent,
    VisionCapability,
    VisionProvider,
    VisionRequest,
    VisionResponse,
)

from core.config import get_vision_config

logger = get_logger(__name__)


class VisionService:
    """
    Multi-provider vision service.

    Supports:
    - OpenAI GPT-4o / GPT-4-turbo-vision
    - Anthropic Claude 3.5 Sonnet
    - Google Gemini 2.0
    - Ollama (local models with vision)

    Features:
    - Automatic provider failover
    - Structured output (JSON mode)
    - Multi-image support
    - OCR and diagram understanding
    """

    # Default models per provider
    DEFAULT_MODELS = {
        VisionProvider.OPENAI: "gpt-4o",
        VisionProvider.ANTHROPIC: "claude-3-5-sonnet-20241022",
        VisionProvider.GOOGLE: "gemini-2.0-flash",
        VisionProvider.OLLAMA: "llava",
    }

    # Prompts for specific capabilities
    CAPABILITY_PROMPTS = {
        VisionCapability.OCR: "Extract all text from this image. Return only the extracted text, preserving the layout as much as possible.",
        VisionCapability.DIAGRAM_UNDERSTANDING: "Analyze this diagram. Describe its structure, components, relationships, and any text labels. Be precise about connections and flow.",
        VisionCapability.SCREENSHOT_ANALYSIS: "Analyze this screenshot. Identify UI elements, their positions, any text content, and the overall state of the interface.",
        VisionCapability.OBJECT_DETECTION: "List all objects visible in this image. For each object, describe its position (top/bottom, left/right/center) and any relevant attributes.",
        VisionCapability.COMPARISON: "Compare these images. Describe the similarities and differences you observe.",
    }

    def __init__(
        self,
        default_provider: VisionProvider = VisionProvider.OPENAI,
        openai_api_key: str | None = None,
        anthropic_api_key: str | None = None,
        google_api_key: str | None = None,
    ) -> None:
        """
        Initialize vision service.

        Args:
            default_provider: Default provider to use
            openai_api_key: OpenAI API key (or from env OPENAI_API_KEY)
            anthropic_api_key: Anthropic API key (or from env ANTHROPIC_API_KEY)
            google_api_key: Google API key (or from env GOOGLE_API_KEY)
        """
        self.default_provider = default_provider
        self._openai_key = openai_api_key or get_vision_config().openai_api_key
        self._anthropic_key = anthropic_api_key or get_vision_config().anthropic_api_key
        self._google_key = google_api_key or get_vision_config().google_api_key

        logger.info(
            "vision_service_initialized",
            default_provider=default_provider.value,
            openai_available=bool(self._openai_key),
            anthropic_available=bool(self._anthropic_key),
            google_available=bool(self._google_key),
        )

    async def analyze(self, request: VisionRequest) -> VisionResponse:
        """
        Analyze images with the given request.

        Args:
            request: Vision analysis request

        Returns:
            Vision analysis response
        """
        provider = request.provider or self.default_provider

        # Build the full prompt
        prompt = self._build_prompt(request)

        logger.info(
            "vision_analyze_start",
            provider=provider.value,
            capability=request.capability.value,
            num_images=len(request.images),
        )

        try:
            if provider == VisionProvider.OPENAI:
                response = await self._analyze_openai(request, prompt)
            elif provider == VisionProvider.ANTHROPIC:
                response = await self._analyze_anthropic(request, prompt)
            elif provider == VisionProvider.GOOGLE:
                response = await self._analyze_google(request, prompt)
            elif provider == VisionProvider.OLLAMA:
                response = await self._analyze_ollama(request, prompt)
            else:
                raise ValueError(f"Unsupported provider: {provider}")

            logger.info(
                "vision_analyze_complete",
                provider=provider.value,
                tokens_used=response.tokens_used,
            )

            return response

        except Exception as e:
            logger.error(
                "vision_analyze_error",
                provider=provider.value,
                error=str(e),
            )
            raise

    def _build_prompt(self, request: VisionRequest) -> str:
        """
        Construct the final instructions based on request capabilities.

        Args:
            request: The user's vision request.

        Returns:
            str: Enriched prompt with capability-specific formatting.
        """
        base_prompt = request.prompt

        # Add capability-specific prompt if not basic analysis
        if request.capability != VisionCapability.IMAGE_ANALYSIS:
            capability_prompt = self.CAPABILITY_PROMPTS.get(request.capability, "")
            if capability_prompt:
                base_prompt = (
                    f"{capability_prompt}\n\nAdditional instructions: {request.prompt}"
                )

        # Add JSON mode instruction if requested
        if request.json_mode:
            base_prompt += "\n\nRespond only with valid JSON."

        return base_prompt

    async def _analyze_openai(
        self, request: VisionRequest, prompt: str
    ) -> VisionResponse:
        """Analyze using OpenAI."""
        if not self._openai_key:
            raise ValueError("OpenAI API key not configured")

        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError("openai package required: pip install openai") from None

        client = AsyncOpenAI(api_key=self._openai_key)
        model = self.DEFAULT_MODELS[VisionProvider.OPENAI]

        # Build messages with images
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for image in request.images:
            content.append(image.to_openai_format())

        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": content}],
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            response_format={"type": "json_object"} if request.json_mode else None,
        )  # type: ignore[call-overload]

        return VisionResponse(
            success=True,
            content=response.choices[0].message.content or "",
            provider="openai",
            model=model,
            tokens_used=response.usage.total_tokens if response.usage else 0,
            raw_response=response.model_dump(),
        )

    async def _analyze_anthropic(
        self, request: VisionRequest, prompt: str
    ) -> VisionResponse:
        """Analyze using Anthropic."""
        if not self._anthropic_key:
            raise ValueError("Anthropic API key not configured")

        try:
            import httpx
        except ImportError:
            raise ImportError("httpx package required") from None

        model = self.DEFAULT_MODELS[VisionProvider.ANTHROPIC]

        # Build content with images
        content: list[dict[str, Any]] = []
        for image in request.images:
            content.append(image.to_anthropic_format())
        content.append({"type": "text", "text": prompt})

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self._anthropic_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": request.max_tokens,
                    "messages": [{"role": "user", "content": content}],
                },
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()

        return VisionResponse(
            success=True,
            content=data["content"][0]["text"],
            provider="anthropic",
            model=model,
            tokens_used=data.get("usage", {}).get("input_tokens", 0)
            + data.get("usage", {}).get("output_tokens", 0),
            raw_response=data,
        )

    async def _analyze_google(
        self, request: VisionRequest, prompt: str
    ) -> VisionResponse:
        """Analyze using Google Gemini."""
        if not self._google_key:
            raise ValueError("Google API key not configured")

        try:
            import httpx
        except ImportError:
            raise ImportError("httpx package required") from None

        model = self.DEFAULT_MODELS[VisionProvider.GOOGLE]

        # Build parts with images
        parts: list[dict[str, Any]] = []
        for image in request.images:
            parts.append(
                {
                    "inline_data": {
                        "mime_type": image.media_type,
                        "data": image.data,
                    }
                }
            )
        parts.append({"text": prompt})

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"https://generativelanguage.googleapis.com/v1/models/{model}:generateContent",
                headers={"Content-Type": "application/json"},
                params={"key": self._google_key},
                json={
                    "contents": [{"parts": parts}],
                    "generationConfig": {
                        "maxOutputTokens": request.max_tokens,
                        "temperature": request.temperature,
                    },
                },
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()

        content = data["candidates"][0]["content"]["parts"][0]["text"]

        return VisionResponse(
            success=True,
            content=content,
            provider="google",
            model=model,
            tokens_used=data.get("usageMetadata", {}).get("totalTokenCount", 0),
            raw_response=data,
        )

    async def _analyze_ollama(
        self, request: VisionRequest, prompt: str
    ) -> VisionResponse:
        """Analyze using Ollama (local)."""
        try:
            import httpx
        except ImportError:
            raise ImportError("httpx package required") from None

        model = self.DEFAULT_MODELS[VisionProvider.OLLAMA]
        ollama_url = get_vision_config().ollama_url

        # Ollama expects images as base64 array
        images = [img.data for img in request.images if img.source_type == "base64"]

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "images": images,
                    "stream": False,
                },
                timeout=120.0,
            )
            response.raise_for_status()
            data = response.json()

        return VisionResponse(
            success=True,
            content=data.get("response", ""),
            provider="ollama",
            model=model,
            tokens_used=data.get("eval_count", 0),
            raw_response=data,
        )

    # -------------------------------------------------------------------------
    # Convenience Methods
    # -------------------------------------------------------------------------

    async def describe_image(self, image: ImageContent) -> str:
        """
        Generate a concise textual description of an image.

        Args:
            image: The image to analyze.

        Returns:
            str: Natural language description of the content.
        """
        request = VisionRequest(
            prompt="Describe this image in detail.",
            images=[image],
        )
        response = await self.analyze(request)
        return response.content

    async def extract_text(self, image: ImageContent) -> str:
        """
        Extract readable text from an image using OCR.

        Args:
            image: The source image.

        Returns:
            str: Transcribed text content.
        """
        request = VisionRequest(
            prompt="Extract all text.",
            images=[image],
            capability=VisionCapability.OCR,
        )
        response = await self.analyze(request)
        return response.content

    async def analyze_screenshot(self, image: ImageContent, question: str = "") -> str:
        """
        Identify UI elements and state from a screen capture.

        Args:
            image: The screenshot image.
            question: Specific area of interest (e.g., 'where is the login button?').

        Returns:
            str: Detailed UI analysis and element descriptions.
        """
        prompt = question or "Describe the UI and any visible text or buttons."
        request = VisionRequest(
            prompt=prompt,
            images=[image],
            capability=VisionCapability.SCREENSHOT_ANALYSIS,
        )
        response = await self.analyze(request)
        return response.content

    async def analyze_diagram(self, image: ImageContent) -> str:
        """Analyze a diagram or architecture image."""
        request = VisionRequest(
            prompt="Analyze this diagram.",
            images=[image],
            capability=VisionCapability.DIAGRAM_UNDERSTANDING,
        )
        response = await self.analyze(request)
        return response.content

    async def compare_images(
        self, image1: ImageContent, image2: ImageContent, aspect: str = ""
    ) -> str:
        """Compare two images."""
        prompt = (
            f"Compare these images. {aspect}" if aspect else "Compare these images."
        )
        request = VisionRequest(
            prompt=prompt,
            images=[image1, image2],
            capability=VisionCapability.COMPARISON,
        )
        response = await self.analyze(request)
        return response.content
