"""
Vision Tools for MCP.

Exposes vision capabilities as MCP-compatible tools for use by agents.

Usage:
    from core.mcp import MCPServer
    from core.services.vision.tools import register_vision_tools

    server = MCPServer()
    register_vision_tools(server)
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from core.observability.logging import get_logger
from core.services.vision.models import (
    ImageContent,
    VisionCapability,
    VisionRequest,
)
from core.services.vision.service import VisionService

if TYPE_CHECKING:
    from core.mcp.server import MCPServer

logger = get_logger(__name__)


def register_vision_tools(
    server: MCPServer, service: VisionService | None = None
) -> None:
    """
    Register vision tools with an MCP server.

    Args:
        server: MCP server instance
        service: Optional VisionService instance (creates default if None)
    """
    vision_service = service or VisionService()

    @server.tool(
        name="analyze_image",
        description="Analyze an image and describe its contents. Supports base64-encoded images or URLs.",
        input_schema={
            "type": "object",
            "properties": {
                "image_data": {
                    "type": "string",
                    "description": "Base64-encoded image data OR image URL",
                },
                "prompt": {
                    "type": "string",
                    "description": "What to analyze or look for in the image",
                    "default": "Describe this image in detail.",
                },
                "is_url": {
                    "type": "boolean",
                    "description": "Set to true if image_data is a URL",
                    "default": False,
                },
            },
            "required": ["image_data"],
        },
    )
    async def analyze_image(
        image_data: str,
        prompt: str = "Describe this image in detail.",
        is_url: bool = False,
    ) -> dict[str, Any]:
        """
        Analyze an image and describe its contents.

        Args:
            image_data: Base64 string or public URL.
            prompt: Question or instruction for analysis.
            is_url: Whether image_data is a URL.

        Returns:
            dict[str, Any]: Analysis results or error info.
        """
        try:
            if is_url:
                image = ImageContent.from_url(image_data)
            else:
                image = ImageContent.from_base64(image_data)

            request = VisionRequest(prompt=prompt, images=[image])
            response = await vision_service.analyze(request)

            return {
                "status": "success",
                "analysis": response.content,
                "provider": response.provider,
                "model": response.model,
            }
        except Exception as e:
            logger.error("vision_tool_error", tool="analyze_image", error=str(e))
            return {"status": "error", "error": str(e)}

    @server.tool(
        name="extract_text_from_image",
        description="Extract text from an image using OCR. Works with screenshots, documents, signs, etc.",
        input_schema={
            "type": "object",
            "properties": {
                "image_data": {
                    "type": "string",
                    "description": "Base64-encoded image data",
                },
            },
            "required": ["image_data"],
        },
    )
    async def extract_text_from_image(image_data: str) -> dict[str, Any]:
        """
        Extract text from an image (OCR).

        Args:
            image_data: Base64-encoded image data.

        Returns:
            dict[str, Any]: Extracted text or error info.
        """
        try:
            image = ImageContent.from_base64(image_data)
            request = VisionRequest(
                prompt="Extract all text.",
                images=[image],
                capability=VisionCapability.OCR,
            )
            response = await vision_service.analyze(request)

            return {
                "status": "success",
                "extracted_text": response.content,
                "provider": response.provider,
            }
        except Exception as e:
            logger.error("vision_tool_error", tool="extract_text", error=str(e))
            return {"status": "error", "error": str(e)}

    @server.tool(
        name="analyze_screenshot",
        description="Analyze a UI screenshot. Identifies buttons, text fields, menus, and other UI elements.",
        input_schema={
            "type": "object",
            "properties": {
                "image_data": {
                    "type": "string",
                    "description": "Base64-encoded screenshot",
                },
                "question": {
                    "type": "string",
                    "description": "Optional specific question about the UI",
                    "default": "",
                },
            },
            "required": ["image_data"],
        },
    )
    async def analyze_screenshot(image_data: str, question: str = "") -> dict[str, Any]:
        """
        Analyze a UI screenshot to identify elements.

        Args:
            image_data: Base64-encoded screenshot.
            question: Optional specific UI question.

        Returns:
            dict[str, Any]: UI analysis or error info.
        """
        try:
            image = ImageContent.from_base64(image_data)
            prompt = (
                question
                or "Describe all visible UI elements, their positions, and any text."
            )

            request = VisionRequest(
                prompt=prompt,
                images=[image],
                capability=VisionCapability.SCREENSHOT_ANALYSIS,
            )
            response = await vision_service.analyze(request)

            return {
                "status": "success",
                "ui_analysis": response.content,
                "provider": response.provider,
            }
        except Exception as e:
            logger.error("vision_tool_error", tool="analyze_screenshot", error=str(e))
            return {"status": "error", "error": str(e)}

    @server.tool(
        name="analyze_diagram",
        description="Analyze a diagram, flowchart, or architecture image. Extracts structure, components, and relationships.",
        input_schema={
            "type": "object",
            "properties": {
                "image_data": {
                    "type": "string",
                    "description": "Base64-encoded diagram image",
                },
            },
            "required": ["image_data"],
        },
    )
    async def analyze_diagram(image_data: str) -> dict[str, Any]:
        """
        Analyze a diagram or flowchart.

        Args:
            image_data: Base64-encoded diagram.

        Returns:
            dict[str, Any]: Diagram interpretation or error info.
        """
        try:
            image = ImageContent.from_base64(image_data)
            request = VisionRequest(
                prompt="Analyze this diagram.",
                images=[image],
                capability=VisionCapability.DIAGRAM_UNDERSTANDING,
            )
            response = await vision_service.analyze(request)

            return {
                "status": "success",
                "diagram_analysis": response.content,
                "provider": response.provider,
            }
        except Exception as e:
            logger.error("vision_tool_error", tool="analyze_diagram", error=str(e))
            return {"status": "error", "error": str(e)}

    @server.tool(
        name="compare_images",
        description="Compare two images and describe similarities and differences.",
        input_schema={
            "type": "object",
            "properties": {
                "image1_data": {
                    "type": "string",
                    "description": "Base64-encoded first image",
                },
                "image2_data": {
                    "type": "string",
                    "description": "Base64-encoded second image",
                },
                "comparison_focus": {
                    "type": "string",
                    "description": "What aspect to focus on when comparing",
                    "default": "",
                },
            },
            "required": ["image1_data", "image2_data"],
        },
    )
    async def compare_images(
        image1_data: str,
        image2_data: str,
        comparison_focus: str = "",
    ) -> dict[str, Any]:
        """
        Compare two images for similarities and differences.

        Args:
            image1_data: Base64-encoded first image.
            image2_data: Base64-encoded second image.
            comparison_focus: Specific aspect to compare.

        Returns:
            dict[str, Any]: Comparison results or error info.
        """
        try:
            image1 = ImageContent.from_base64(image1_data)
            image2 = ImageContent.from_base64(image2_data)

            prompt = (
                f"Compare these two images. {comparison_focus}"
                if comparison_focus
                else "Compare these two images."
            )

            request = VisionRequest(
                prompt=prompt,
                images=[image1, image2],
                capability=VisionCapability.COMPARISON,
            )
            response = await vision_service.analyze(request)

            return {
                "status": "success",
                "comparison": response.content,
                "provider": response.provider,
            }
        except Exception as e:
            logger.error("vision_tool_error", tool="compare_images", error=str(e))
            return {"status": "error", "error": str(e)}

    @server.tool(
        name="find_ui_element",
        description="Find a specific UI element in a screenshot by description. Returns element location.",
        input_schema={
            "type": "object",
            "properties": {
                "image_data": {
                    "type": "string",
                    "description": "Base64-encoded screenshot",
                },
                "element_description": {
                    "type": "string",
                    "description": "Description of the element to find (e.g., 'blue Submit button', 'search input field')",
                },
            },
            "required": ["image_data", "element_description"],
        },
    )
    async def find_ui_element(
        image_data: str, element_description: str
    ) -> dict[str, Any]:
        """
        Find a UI element by description and return its location.

        Args:
            image_data: Base64-encoded screenshot.
            element_description: Visual description of the target element.

        Returns:
            dict[str, Any]: Element details and position if found.
        """
        try:
            image = ImageContent.from_base64(image_data)

            prompt = f"""Find the UI element matching this description: "{element_description}"

Respond in JSON format with:
{{
    "found": true/false,
    "element_type": "button/input/link/etc",
    "text": "visible text if any",
    "position": {{
        "relative": "top-left/top-center/center/bottom-right/etc",
        "approximate_x_percent": 0-100,
        "approximate_y_percent": 0-100
    }},
    "confidence": "high/medium/low"
}}"""

            request = VisionRequest(
                prompt=prompt,
                images=[image],
                capability=VisionCapability.SCREENSHOT_ANALYSIS,
                json_mode=True,
            )
            response = await vision_service.analyze(request)

            return {
                "status": "success",
                "result": response.as_json or response.content,
                "provider": response.provider,
            }
        except Exception as e:
            logger.error("vision_tool_error", tool="find_ui_element", error=str(e))
            return {"status": "error", "error": str(e)}

    logger.info("vision_tools_registered", tool_count=6)
