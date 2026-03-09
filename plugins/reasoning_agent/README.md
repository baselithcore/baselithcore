# Reasoning Agent Plugin

## Overview

The `reasoning_agent` plugin introduces advanced cognitive capabilities to the BaselithCore framework by implementing the **Tree of Thoughts (ToT)** algorithm. It allows agents to solve complex, multi-step problems by actively planning, exploring multiple reasoning paths (branches), and evaluating the best course of action before generating a final response.

## Why is it a Plugin?

Most conversational agents and simple RAG (Retrieval-Augmented Generation) applications only require linear, single-pass responses (like Chain of Thought). The Tree of Thoughts algorithm is highly token-intensive and computationally expensive. By keeping it as a standalone plugin:

1. The core framework remains incredibly fast for linear tasks.
2. Developers can explicitly enable complex reasoning only for the agents or intents that strictly require it (e.g., coding assistants, complex data analyzers, mathematical solvers).

## Core Components

- `ReasoningAgent`: The main agent class that wraps the ToT engine and optionally hooks into the `SandboxService` for executing code to validate its own thoughts.
- `ReasoningAgentPlugin`: The plugin wrapper that registers the agent and maps it to specific "intents" (e.g., "analyze", "plan", "solve").
- `TreeOfThoughtsAsync`: The asynchronous engine (provided by the core) that drives the branching logic.

## Usage

To use the Reasoning Agent in your application:

1. **Enable the plugin** in `configs/plugins.yaml`:

   ```yaml
   reasoning_agent:
     enabled: true
     max_steps: 5          # How deep the tree can go
     branching_factor: 3   # How many alternative thoughts to generate per step
   ```

2. **Triggering the Agent**:

   Because the plugin registers an intent pattern, any user message containing keywords like `"analizza"`, `"solve"`, `"step by step"`, or `"pianifica"` will automatically be routed to the `ReasoningFlowHandler`.

3. **Direct Usage (Code)**:

   ```python
   from plugins.reasoning_agent.reasoning_agent import ReasoningAgent

   agent = ReasoningAgent(service=my_llm_service, sandbox_service=my_sandbox)
   
   # Solve a complex problem
   result = await agent.solve(
       problem_description="Write a python script to calculate the 100th Fibonacci number, then optimize it.",
       max_steps=5,
       branching_factor=3
   )
   
   print(result["best_solution"])
   print(result["tree_visualization"]) # Shows the branching paths evaluated
   ```

## Development

This plugin can be extended by providing it with custom `tools` (passed into the `solve()` method). By default, it attempts to use the `SandboxService` to run and verify code snippets during its reasoning steps.
