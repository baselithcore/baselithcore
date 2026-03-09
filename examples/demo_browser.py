#!/usr/bin/env python3
"""
Demo: Browser Agent - Autonomous Web Navigation.

This demo showcases the BrowserAgent capabilities:
- Navigate to URLs
- Take screenshots
- Execute complex multi-step tasks autonomously

Prerequisites:
    pip install playwright
    playwright install chromium
    export OPENAI_API_KEY=your-key

Usage:
    python examples/demo_browser.py [url]
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.agents import BrowserAgent


async def demo_navigate_and_screenshot(agent: BrowserAgent, url: str) -> None:
    """Demonstrate navigation and screenshot."""
    print("\n" + "=" * 60)
    print("🌐 DEMO: Navigate and Screenshot")
    print("=" * 60)

    print(f"🔗 Navigating to: {url}")

    await agent.navigate(url)
    print("✅ Navigation complete")

    # Take screenshot
    screenshot_path = Path("demo_screenshot.png")
    screenshot = await agent.screenshot()

    if screenshot:
        screenshot_path.write_bytes(screenshot)
        print(f"📷 Screenshot saved: {screenshot_path.absolute()}")
    else:
        print("⚠️  Could not capture screenshot")


async def demo_autonomous_task(agent: BrowserAgent) -> None:
    """Demonstrate autonomous task execution."""
    print("\n" + "=" * 60)
    print("🤖 DEMO: Autonomous Task Execution")
    print("=" * 60)

    task = "Go to google.com, search for 'Python programming', and describe the first result"

    print(f"📝 Task: {task}")
    print("⏳ Executing task autonomously...")

    result = await agent.execute_task(task)

    if result.success:
        print(f"✅ Task completed in {result.steps_taken} steps")
        print(f"📄 Result:\n{result.final_response}")
    else:
        print(f"❌ Task failed: {result.error}")


async def demo_element_interaction(agent: BrowserAgent) -> None:
    """Demonstrate element interaction."""
    print("\n" + "=" * 60)
    print("🖱️ DEMO: Element Interaction")
    print("=" * 60)

    print("🔗 Navigating to example.com...")
    await agent.navigate("https://example.com")

    print("📷 Analyzing page...")
    elements = await agent.find_elements("a")  # Find all links

    print(f"🔍 Found {len(elements)} links on the page")
    for i, elem in enumerate(elements[:5], 1):
        print(f"   {i}. {elem.get('text', 'N/A')} -> {elem.get('href', 'N/A')}")


async def main() -> None:
    """Run all demos."""
    print("🚀 Browser Agent Demo")
    print("=" * 60)

    # Get URL from args or use default
    url = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"

    async with BrowserAgent(headless=True) as agent:
        print("✅ BrowserAgent initialized (headless mode)")

        await demo_navigate_and_screenshot(agent, url)

        # Only run autonomous demo if explicitly requested
        if "--autonomous" in sys.argv:
            await demo_autonomous_task(agent)
        else:
            print(
                "\n💡 Tip: Run with --autonomous flag to see autonomous task execution"
            )

    print("\n" + "=" * 60)
    print("🎉 All demos completed!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
