#!/usr/bin/env python3
"""
Demo: Coding Agent - Auto-Debug Loop.

This demo showcases the CodingAgent capabilities:
- Generate code from natural language description
- Auto-debug loop (Execute → Error → Fix → Retry)
- Generate unit tests for existing code

Prerequisites:
    export OPENAI_API_KEY=your-key

Usage:
    python examples/demo_coding.py
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.agents import CodingAgent


async def demo_generate_code(agent: CodingAgent) -> None:
    """Demonstrate code generation with auto-debug."""
    print("\n" + "=" * 60)
    print("💻 DEMO: Code Generation with Auto-Debug")
    print("=" * 60)

    task = "Write a function that calculates the n-th Fibonacci number recursively with memoization"

    print(f"📝 Task: {task}")
    print("⏳ Generating code...")

    result = await agent.generate_code(task)

    if result.success:
        print(f"✅ Code generated in {result.iterations} iteration(s)")
        print(f"\n📄 Generated Code:\n{'-' * 40}")
        print(result.final_code)
        print("-" * 40)
    else:
        print(f"❌ Failed: {result.error}")


async def demo_fix_code(agent: CodingAgent) -> None:
    """Demonstrate auto-fix capability."""
    print("\n" + "=" * 60)
    print("🔧 DEMO: Auto-Fix Buggy Code")
    print("=" * 60)

    buggy_code = """
def divide(a, b):
    return a / b

result = divide(10, 0)
print(result)
"""
    error_message = "ZeroDivisionError: division by zero"

    print(f"🐛 Buggy Code:\n{buggy_code}")
    print(f"❌ Error: {error_message}")
    print("⏳ Fixing...")

    result = await agent.fix_code(buggy_code, error_message)

    if result.success:
        print(f"✅ Fixed in {result.iterations} iteration(s)")
        print(f"\n📄 Fixed Code:\n{'-' * 40}")
        print(result.final_code)
        print("-" * 40)
    else:
        print(f"❌ Could not fix: {result.error}")


async def demo_generate_tests(agent: CodingAgent) -> None:
    """Demonstrate test generation."""
    print("\n" + "=" * 60)
    print("🧪 DEMO: Generate Unit Tests")
    print("=" * 60)

    code_to_test = '''
def is_palindrome(s: str) -> bool:
    """Check if a string is a palindrome."""
    cleaned = ''.join(c.lower() for c in s if c.isalnum())
    return cleaned == cleaned[::-1]


def factorial(n: int) -> int:
    """Calculate factorial of n."""
    if n < 0:
        raise ValueError("n must be non-negative")
    if n <= 1:
        return 1
    return n * factorial(n - 1)
'''

    print(f"📄 Code to test:\n{code_to_test}")
    print("⏳ Generating tests...")

    result = await agent.generate_tests(code_to_test)

    if result.success:
        print("✅ Tests generated")
        print(f"\n🧪 Test Code:\n{'-' * 40}")
        print(result.final_code)
        print("-" * 40)
    else:
        print(f"❌ Failed: {result.error}")


async def main() -> None:
    """Run all demos."""
    print("🚀 Coding Agent Demo")
    print("=" * 60)

    # Initialize agent
    agent = CodingAgent(max_fix_attempts=3)
    print(f"✅ CodingAgent initialized (max retries: {agent.max_fix_attempts})")

    await demo_generate_code(agent)
    await demo_fix_code(agent)
    await demo_generate_tests(agent)

    print("\n" + "=" * 60)
    print("🎉 All demos completed!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
