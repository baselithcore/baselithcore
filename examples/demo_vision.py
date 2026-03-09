#!/usr/bin/env python3
"""
Demo: Vision Service - Multi-Modal Image Analysis.

This demo showcases the VisionService capabilities:
- Analyzing images with natural language prompts
- Extracting text (OCR) from images
- Screenshot analysis for UI understanding

Prerequisites:
    export OPENAI_API_KEY=your-key

Usage:
    python examples/demo_vision.py [image_path]
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.services.vision import VisionService, VisionRequest, ImageContent


async def demo_analyze_image(service: VisionService, image_path: str) -> None:
    """Demonstrate basic image analysis."""
    print("\n" + "=" * 60)
    print("🔍 DEMO: Image Analysis")
    print("=" * 60)

    image = ImageContent.from_file(image_path)
    request = VisionRequest(
        prompt="Describe this image in detail. What do you see?",
        images=[image],
    )

    print(f"📷 Analyzing: {image_path}")
    response = await service.analyze(request)

    print(f"✅ Provider: {response.provider}")
    print(f"📝 Description:\n{response.content}")
    print(f"🔢 Tokens used: {response.tokens_used}")


async def demo_ocr(service: VisionService, image_path: str) -> None:
    """Demonstrate OCR (text extraction)."""
    print("\n" + "=" * 60)
    print("📝 DEMO: OCR Text Extraction")
    print("=" * 60)

    image = ImageContent.from_file(image_path)
    text = await service.extract_text(image)

    print(f"📷 Extracting text from: {image_path}")
    print(f"📄 Extracted Text:\n{text}")


async def demo_screenshot_analysis(service: VisionService, image_path: str) -> None:
    """Demonstrate screenshot UI analysis."""
    print("\n" + "=" * 60)
    print("🖥️ DEMO: Screenshot UI Analysis")
    print("=" * 60)

    image = ImageContent.from_file(image_path)
    analysis = await service.analyze_screenshot(
        image, "Identify all buttons and interactive elements."
    )

    print(f"📷 Analyzing UI: {image_path}")
    print(f"🎯 UI Elements:\n{analysis}")


async def main() -> None:
    """Run all demos."""
    print("🚀 Vision Service Demo")
    print("=" * 60)

    # Initialize service
    service = VisionService()
    print(f"✅ VisionService initialized (default: {service.default_provider.value})")

    # Check for image argument
    if len(sys.argv) > 1:
        image_path = sys.argv[1]
        if not Path(image_path).exists():
            print(f"❌ Image not found: {image_path}")
            return

        await demo_analyze_image(service, image_path)
        await demo_ocr(service, image_path)
        await demo_screenshot_analysis(service, image_path)
    else:
        print("\n⚠️  No image provided. Usage: python demo_vision.py <image_path>")
        print("   Example: python demo_vision.py screenshot.png")

        # Demo with URL
        print("\n📷 Demo with sample URL:")
        image = ImageContent.from_url(
            "https://upload.wikimedia.org/wikipedia/commons/thumb/3/3a/Cat03.jpg/1200px-Cat03.jpg"
        )
        request = VisionRequest(prompt="What animal is this?", images=[image])
        response = await service.analyze(request)
        print(f"   Answer: {response.content}")


if __name__ == "__main__":
    asyncio.run(main())
