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

Enable the plugin in `configs/plugins.yaml` (it ships disabled):

```yaml
reasoning_agent:
  enabled: true
  max_steps: 5          # Maximum tree depth (default 5)
  branching_factor: 3   # Thoughts generated per expansion (default 3)
```

`ReasoningAgentPlugin.get_flow_handlers()` builds the handler as
`ReasoningFlowHandler(agent, config_provider=self.get_config)`, so the two
tuning keys are read from this entry through the plugin's
`get_config(key, default)`. For every request the handler resolves each value
in this order:

1. the `max_steps` / `branching_factor` key in the request `context` dict;
2. the `reasoning_agent:` entry in `configs/plugins.yaml`;
3. the built-in defaults, `5` and `3` (also used when the config value is
   `None` or the lookup raises).

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

`ReasoningAgent` wraps `core.reasoning.tot.TreeOfThoughtsAsync`. `solve()` forwards the request as `tot_engine.solve(problem=..., k=branching_factor, max_steps=max_steps, tools=[sandbox])` (the `tools` list is empty when no `SandboxService` was injected) and does not override the engine's `strategy` argument, so the search runs with the engine default, `"mcts"` — Monte Carlo Tree Search over the thought tree (`"bfs"` is the alternative). Each generated thought is scored by an LLM call built from `THOUGHT_EVALUATION_PROMPT`; sibling evaluations are fanned out concurrently and routed through the shared `ThoughtCache`, so identical thoughts reuse an earlier score, and a failed evaluation scores `0.0`. The result dict carries `best_solution`, `steps` (the path from the root to the best leaf) and `tree_visualization` (a Mermaid export of the whole tree).

!!! tip "Sandbox Integration"
    For technical tasks, provide a `SandboxService` to the agent — `ReasoningAgentPlugin.create_agent()` does so automatically when `core.services.sandbox.service` is importable. The sandbox is handed to the ToT engine in its `tools` list, which the engine can use during thought execution.
