"""
Vision Flow Handler.

Handles vision-related intents by routing to VisionService.
"""

from core.observability.logging import get_logger
from typing import Any, Dict
from core.orchestration.handlers import BaseFlowHandler
from core.services.vision import (
    VisionService,
    VisionRequest,
    ImageContent,
    VisionCapability,
)

logger = get_logger(__name__)


class VisionHandler(BaseFlowHandler):
    """
    Handler for 'vision_analysis' intent.
    Routes requests to VisionService.
    """

    def __init__(self, *args, **kwargs):
        """
        Initialize the vision handler.

        Args:
            *args, **kwargs: Passed to BaseFlowHandler.
        """
        super().__init__(*args, **kwargs)
        # Initialize service (will pick up keys from env)
        self.vision_service = VisionService()

    async def handle(self, query: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle vision analysis requests.

        Extracts images from context, determines the appropriate vision
        capability (OCR, Analysis, etc.), and invokes the VisionService.

        Args:
            query: The descriptive query for analysis.
            context: Context containing 'image_paths' or 'image_data'.

        Returns:
            Dict[str, Any]: A dictionary with the analysis 'response' and 'metadata'.
        """
        try:
            logger.info(f"Starting vision analysis for query: {query}")

            # Extract images from context
            # Context comes from Orchestrator -> usually constructed from request
            # We assume 'image_data' or 'image_paths' is populated
            image_paths = context.get("image_paths", [])
            image_data = context.get("image_data", [])  # base64 strings

            if not image_paths and not image_data:
                return {
                    "response": "To analyze an image, you must provide one via path or data.",
                    "error": True,
                }

            images = []
            for path in image_paths:
                images.append(ImageContent.from_file(path))
            for data in image_data:
                images.append(ImageContent.from_base64(data))

            # Determine capability based on query?
            # For now default to general image analysis
            capability = VisionCapability.IMAGE_ANALYSIS
            if "screenshot" in query.lower():
                capability = VisionCapability.SCREENSHOT_ANALYSIS
            elif (
                "ocr" in query.lower()
                or "testo" in query.lower()
                or "text" in query.lower()
            ):
                capability = VisionCapability.OCR

            request = VisionRequest(prompt=query, images=images, capability=capability)

            result = await self.vision_service.analyze(request)

            return {
                "response": result.content,
                "metadata": {
                    "provider": result.provider,
                    "model": result.model,
                    "tokens": result.tokens_used,
                },
            }

        except Exception as e:
            logger.error(f"Error in Vision Handler: {e}")
            return {
                "response": "An error occurred during image analysis.",
                "error": True,
                "metadata": {"error": str(e)},
            }
