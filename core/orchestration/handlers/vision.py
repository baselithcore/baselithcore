"""
Vision Flow Handler.

Handles vision-related intents by routing to VisionService.
"""

import asyncio
from typing import Any

from core.observability.logging import get_logger
from core.orchestration.handlers import BaseFlowHandler
from core.services.vision import (
    ImageContent,
    VisionCapability,
    VisionRequest,
    VisionService,
)

logger = get_logger(__name__)


class VisionHandler(BaseFlowHandler):
    """
    Handler for 'vision_analysis' intent.
    Routes requests to VisionService.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """
        Initialize the vision handler.

        Args:
            *args, **kwargs: Passed to BaseFlowHandler.
        """
        super().__init__(*args, **kwargs)
        # Initialize service (will pick up keys from env)
        self.vision_service = VisionService()

    async def handle(self, query: str, context: dict[str, Any]) -> dict[str, Any]:
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
            # DEBUG, truncated: the raw user query is free-text PII — it must
            # not land in INFO-level aggregated logs.
            logger.debug(f"Starting vision analysis for query: {query[:80]}")

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

            # from_file does sync open() + base64 of the whole image: offload
            # each load to a worker thread and run the batch concurrently
            # instead of serially blocking the event loop.
            images = list(
                await asyncio.gather(
                    *(
                        asyncio.to_thread(ImageContent.from_file, path)
                        for path in image_paths
                    )
                )
            )
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
