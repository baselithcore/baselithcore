"""
Multi-Modal Reasoning Handler.

Combines Vision analysis with Tree of Thoughts reasoning
for complex visual understanding tasks.

Use cases:
- Technical diagram analysis with step-by-step reasoning
- Architecture document understanding
- Visual debugging of code/UI
- Complex image-based problem solving
"""

from core.observability.logging import get_logger
from typing import Any, Dict, List, Optional

from core.orchestration.handlers import BaseFlowHandler
from core.services.llm import get_llm_service
from core.services.vision import (
    VisionService,
    VisionRequest,
    ImageContent,
    VisionCapability,
)
from core.reasoning.tot.engine import TreeOfThoughtsAsync

logger = get_logger(__name__)


class MultiModalReasoningHandler(BaseFlowHandler):
    """
    Handler for 'multimodal_reasoning' intent.

    Combines Vision analysis with Tree of Thoughts reasoning to enable
    deep understanding and problem-solving with visual inputs.

    Workflow:
    1. Analyze images with VisionService to extract visual context
    2. Enrich the problem description with visual insights
    3. Apply ToT reasoning on the enriched context
    4. Return combined result with visual analysis + reasoning traces

    Example:
        ```python
        handler = MultiModalReasoningHandler()
        result = await handler.handle(
            query="Analyze this diagram and explain the data flow",
            context={"image_paths": ["architecture.png"]}
        )
        ```
    """

    def __init__(
        self,
        *args,
        vision_service: Optional[VisionService] = None,
        llm_service: Optional[Any] = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.vision_service = vision_service or VisionService()
        self._llm_service = llm_service
        self._tot_engine: Optional[TreeOfThoughtsAsync] = None

    @property
    def llm_service(self):
        """Lazy load LLM service."""
        if self._llm_service is None:
            self._llm_service = get_llm_service()
        return self._llm_service

    @property
    def tot_engine(self) -> TreeOfThoughtsAsync:
        """Lazy load ToT engine."""
        if self._tot_engine is None:
            self._tot_engine = TreeOfThoughtsAsync(llm_service=self.llm_service)
        return self._tot_engine

    async def handle(self, query: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle multi-modal reasoning request.

        Args:
            query: User's question/request about the image(s)
            context: Must contain either:
                - image_paths: List of file paths to images
                - image_data: List of base64 encoded images

        Returns:
            Dict with:
                - response: Final reasoned answer
                - vision_analysis: Raw vision analysis
                - reasoning_steps: ToT reasoning trace
                - tree_data: Optional mermaid diagram of thought tree
        """
        try:
            logger.info(f"Starting multi-modal reasoning for: {query[:100]}...")

            # Step 1: Extract and validate images
            images = await self._extract_images(context)
            if not images:
                return self._error_response(
                    "No image provided. For multi-modal analysis, "
                    "please provide at least one image."
                )

            # Step 2: Analyze images with Vision
            vision_result = await self._analyze_images(images, query)
            if not vision_result:
                return self._error_response("Unable to analyze the provided images.")

            # Step 3: Create enriched problem for reasoning
            enriched_problem = self._create_enriched_problem(query, vision_result)

            # Step 4: Apply Tree of Thoughts reasoning
            reasoning_params = self._get_reasoning_params(context)
            tot_result = await self.tot_engine.solve(
                problem=enriched_problem, **reasoning_params
            )

            # Step 5: Synthesize final response
            solution = tot_result.get("solution", "No solution found.")
            steps = tot_result.get("steps", [])

            return {
                "response": solution,
                "vision_analysis": vision_result["content"],
                "reasoning_steps": steps,
                "tree_data": tot_result.get("tree_data"),
                "metadata": {
                    "images_analyzed": len(images),
                    "vision_provider": vision_result.get("provider", "unknown"),
                    "reasoning_strategy": reasoning_params.get("strategy", "bfs"),
                    "reasoning_depth": len(steps),
                },
            }

        except Exception as e:
            logger.error(f"Error in MultiModalReasoningHandler: {e}", exc_info=True)
            return self._error_response(str(e))

    async def _extract_images(self, context: Dict[str, Any]) -> List[ImageContent]:
        """
        Extract ImageContent objects from context.

        Parses 'image_paths' or 'image_data' (base64) provided in the
        execution context.

        Args:
            context: The original request context.

        Returns:
            List[ImageContent]: A prepared list of images for vision analysis.
        """
        images: List[ImageContent] = []

        # From file paths
        for path in context.get("image_paths", []):
            try:
                images.append(ImageContent.from_file(path))
            except Exception as e:
                logger.warning(f"Failed to load image from {path}: {e}")

        # From base64 data
        for data in context.get("image_data", []):
            try:
                images.append(ImageContent.from_base64(data))
            except Exception as e:
                logger.warning(f"Failed to parse base64 image: {e}")

        # From URLs (if supported)
        for url in context.get("image_urls", []):
            try:
                images.append(ImageContent.from_url(url))
            except Exception as e:
                logger.warning(f"Failed to load image from URL {url}: {e}")

        return images

    async def _analyze_images(
        self,
        images: List[ImageContent],
        query: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Analyze images with VisionService to provide context for reasoning.

        Args:
            images: List of prepared ImageContent items.
            query: The original prompt to guide the vision system.

        Returns:
            Optional[Dict[str, Any]]: Vision analysis results and metadata.
        """
        try:
            # Create comprehensive vision prompt
            vision_prompt = (
                f"Carefully analyze this image/these images. "
                f"The user asks: {query}\n\n"
                f"Provide a detailed description of:\n"
                f"1. What the image shows\n"
                f"2. Key elements relevant to the question\n"
                f"3. Visible relationships and connections\n"
                f"4. Technical details if present (text, numbers, symbols)"
            )

            request = VisionRequest(
                prompt=vision_prompt,
                images=images,
                capability=VisionCapability.IMAGE_ANALYSIS,
            )

            result = await self.vision_service.analyze(request)

            return {
                "content": result.content,
                "provider": result.provider,
                "model": result.model,
                "tokens": result.tokens_used,
            }

        except Exception as e:
            logger.error(f"Vision analysis failed: {e}")
            return None

    def _create_enriched_problem(
        self,
        original_query: str,
        vision_result: Dict[str, Any],
    ) -> str:
        """
        Combine user query and vision analysis into an enriched problem description.

        Args:
            original_query: User message.
            vision_result: Description and metadata from the vision service.

        Returns:
            str: A prompt suitable for the multi-step reasoning engine.
        """
        return (
            f"## Visual Context\n"
            f"The analysis of the provided image reveals:\n"
            f"{vision_result['content']}\n\n"
            f"## User Question\n"
            f"{original_query}\n\n"
            f"## Instructions\n"
            f"Based on the visual context above, answer the user's question "
            f"in a detailed and structured manner. Make specific references to the "
            f"visual elements in your response."
        )

    def _get_reasoning_params(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract reasoning parameters (k, max_steps, strategy) from context.

        Args:
            context: Execution context.

        Returns:
            Dict[str, Any]: Parameters for the ToT engine.
        """
        return {
            "k": context.get("branching_factor", 3),
            "max_steps": context.get("max_reasoning_steps", 4),
            "strategy": context.get("strategy", "bfs"),
        }

    def _error_response(self, error_message: str) -> Dict[str, Any]:
        """
        Create standardized error response for multimodal reasoning.

        Args:
            error_message: Detailed failure info.

        Returns:
            Dict[str, Any]: Error-formatted handler result.
        """
        return {
            "response": f"Error in multi-modal analysis: {error_message}",
            "error": True,
            "vision_analysis": None,
            "reasoning_steps": [],
            "metadata": {"error": error_message},
        }
