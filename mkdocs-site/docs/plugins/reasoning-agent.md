---
title: Reasoning Agent
description: Advanced cognitive capabilities with Tree of Thoughts (ToT)
---

The `reasoning_agent` plugin introduces advanced cognitive capabilities to the BaselithCore framework by implementing the **Tree of Thoughts (ToT)** algorithm. It allows agents to solve complex, multi-step problems by actively planning, exploring multiple reasoning paths (branches), and evaluating the best course of action before generating a final response.

## Why is it a Plugin?

Most conversational agents and simple RAG (Retrieval-Augmented Generation) applications only require linear, single-pass responses (like Chain of Thought). The Tree of Thoughts algorithm is highly token-intensive and computationally expensive. By keeping it as a standalone plugin:

1. **Performance**: The core framework remains incredibly fast for linear tasks.
2. **Efficiency**: Developers can explicitly enable complex reasoning only for the agents or intents that strictly require it (e.g., coding assistants, complex data analyzers, mathematical solvers).

---

## Core Components

- `ReasoningAgent`: The main agent class that wraps the ToT engine and optionally hooks into the `SandboxService` for executing code to validate its own thoughts.
- `ReasoningAgentPlugin`: The plugin wrapper that registers the agent and maps it to specific "intents".
- `TreeOfThoughtsAsync`: The asynchronous engine (provided by the core) that drives the branching logic.

---

## Usage

### 1. Enable the Plugin

Enable the plugin in `configs/plugins.yaml`:

```yaml
reasoning_agent:
  enabled: true
  max_steps: 5          # How deep the tree can go
  branching_factor: 3   # How many alternative thoughts to generate per step
```

### 2. Triggering the Agent

Because the plugin registers an intent pattern, any user message containing keywords like `"analyze"`, `"solve"`, `"step by step"`, or `"plan"` will automatically be routed to the `ReasoningFlowHandler`.

### 3. Programmatic Usage

```python
from plugins.reasoning_agent.reasoning_agent import ReasoningAgent

agent = ReasoningAgent(service=my_llm_service, sandbox_service=my_sandbox)

# Solve a complex problem
result = await agent.solve(
    problem_description="Identify the bottlenecks in this distributed system architecture...",
    max_steps=5,
    branching_factor=3
)

print(result["best_solution"])
print(result["tree_visualization"]) # Shows the branching paths evaluated
```

---

## Technical Details

The Reasoning Agent utilizes the **Monte Carlo Tree Search (MCTS)** patterns implemented in `core/reasoning` to navigate the thought space. It evaluates each "thought" using a reward function that can be LLM-based or heuristic-based.

!!! tip "Sandbox Integration"
    For technical tasks, provided a `SandboxService` to the agent. It will attempt to verify its reasoning by running code simulations in a secure environment.
