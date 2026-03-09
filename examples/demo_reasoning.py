import asyncio
import os
from typing import Optional

# Standard imports for Baselith-Core

from plugins.reasoning_agent.reasoning_agent import ReasoningAgent
from core.services.llm.service import LLMService

class MockLLMProvider:
    def generate(self, prompt: str, model: str = None, json_mode: bool = False):
        # Return dummy thoughts for Game of 24
        if "generate" in prompt.lower() or "thought" in prompt.lower():
            return "1. 8 - 5 = 3\n2. 3 + 8 = 11\n3. 4 * 3 = 12 (No)", 10
        elif "evaluate" in prompt.lower() or "value" in prompt.lower():
            return "0.5", 5 # score
        return "I am thinking...", 5

    def generate_stream(self, prompt: str, model: str = None):
        yield "Thinking...", 5

class MockLLMService:
    def __init__(self):
        self.provider = MockLLMProvider()
    
    def generate_response(self, prompt: str, model: str = None, json: bool = False) -> str:
        # Simple heuristic response generation for the demo
        if "Game of 24" in prompt:
            return "Initial analysis: We need to reach 24 using 3, 3, 8, 8."
        if "next steps" in prompt or "thoughts" in prompt:
            return (
                "Strategy 1: (8 / (3 - 8/3)) is a known solution for 3,3,8,8 but here we have 4,5,8,8? \n"
                "Wait, the problem says 3,3,8,8. \n"
                "Thought 1: 8 / (3 - 8/3) = 8 / (1/3) = 24. Valid.\n"
                "Thought 2: 3 * 8 = 24. But we have another 3 and 8 left. 3/8? No.\n"
                "Thought 3: 8 + 8 + 3 + 3 = 22. Close."
            )
        if "evaluate" in prompt or "score" in prompt:
            if "8 / (3 - 8/3)" in prompt or "8 / (1/3)" in prompt:
                return "1.0"
            return "0.3"
            
        return "I am thinking about step..."

async def main():
    print("🚀 Starting Reasoning Agent Demo: Tree of Thoughts")
    print("-------------------------------------------------")
    
    # Initialize Service
    llm_service = None
    
    # Check for OpenAI API Key and configure accordingly
    openai_key = os.environ.get("EVAL_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")

    if openai_key:
        print("🔑 Found OpenAI API Key, using OpenAI provider.")
        os.environ["LLM_PROVIDER"] = "openai"
        os.environ["LLM_API_KEY"] = openai_key
        os.environ["LLM_MODEL"] = "gpt-4o"
        try:
            llm_service = LLMService()
        except Exception as e:
            print(f"❌ Failed to init real LLMService: {e}")
    else:
        print("⚠️ No OpenAI API Key found. Using Mock LLM for demonstration.")
        llm_service = MockLLMService()

    # Initialize Agent
    agent = ReasoningAgent(service=llm_service)
    
    # Define Problem
    problem = (
        "Solve the Game of 24. "
        "Use the numbers 3, 3, 8, 8 exactly once each, combined with basic arithmetic operations (+, -, *, /) and parentheses "
        "to yield the value 24. "
        "Show your reasoning step-by-step."
    )
    
    print(f"🧩 Problem: {problem}\n")
    print("🤔 Agent is thinking (this might take a moment)...\n")
    
    try:
        solution = await agent.solve(problem, max_steps=3, branching_factor=2)
        print("💡 Final Result:")
        print("-------------------------------------------------")
        print(solution)
        print("-------------------------------------------------")
    except Exception as e:
        print(f"❌ Error during execution: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
