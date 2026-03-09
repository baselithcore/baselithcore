# Agentic Modules

The `core/agents/` module provides two built-in autonomous agents: **BrowserAgent** and **CodingAgent**. Both implement secure, async-first patterns with dependency injection.

## Module Structure

```txt
core/agents/
├── browser_agent.py       # ReAct-loop browser automation
├── browser_tools.py       # LangChain-compatible browser tools
├── browser_types.py       # Types: BrowserAction, PageState, BrowserAgentResult
├── coding/
│   ├── agent.py           # Auto-debug coding agent
│   ├── prompts.py         # Structured prompts (generate, fix, test, explain)
│   └── types.py           # CodeLanguage, CodingResult, CodeExecutionResult
└── coding_tools.py        # LangChain-compatible coding tools
```

---

## BrowserAgent

An autonomous agent that controls a web browser via **Playwright**, using the Vision service for page understanding.

### Architecture

Implements the **ReAct (Reason + Act)** loop:

```mermaid
graph LR
    A[Observe<br/>Screenshot + URL] --> B[Think<br/>LLM Decision]
    B --> C[Act<br/>Click / Type / Navigate]
    C --> D{Goal<br/>Achieved?}
    D -- No --> A
    D -- Yes --> E[Return Result]
```

### Usage

```python
from core.agents import BrowserAgent

# Context manager handles Playwright lifecycle
async with BrowserAgent(headless=True) as agent:
    result = await agent.execute_task(
        "Go to google.com and search for 'Python tutorials'"
    )
    print(result.success)      # True
    print(result.final_url)    # https://www.google.com/search?q=...
    print(result.steps_taken)  # Number of actions taken
```

### Configuration

| Parameter         | Default    | Description                           |
| ----------------- | ---------- | ------------------------------------- |
| `max_steps`       | `20`       | Maximum ReAct iterations              |
| `headless`        | `True`     | Run browser headlessly                |
| `viewport_width`  | `1280`     | Browser viewport width                |
| `viewport_height` | `720`      | Browser viewport height               |
| `vision_provider` | `"ollama"` | Vision LLM provider for page analysis |

### Supported Actions

| Action     | Description   | Example                                                    |
| ---------- | ------------- | ---------------------------------------------------------- |
| `navigate` | Go to a URL   | `{"action": "navigate", "value": "https://..."}`           |
| `click`    | Click element | `{"action": "click", "selector": "button.submit"}`         |
| `type`     | Type text     | `{"action": "type", "selector": "input", "value": "text"}` |
| `scroll`   | Scroll page   | `{"action": "scroll", "value": "down"}`                    |
| `wait`     | Wait seconds  | `{"action": "wait", "value": "2"}`                         |
| `extract`  | Extract text  | `{"action": "extract", "selector": ".result"}`             |
| `done`     | Goal complete | `{"action": "done", "reasoning": "task done"}`             |

### LangChain Tools Integration

```python
from core.agents.browser_tools import register_browser_tools

# Register as LangChain tools for use in an agent chain
tools = register_browser_tools()
```

---

## CodingAgent

An autonomous coding agent with an **auto-debug loop** for code generation, testing, and refactoring. Executes code securely via the [Sandbox Service](services.md).

**Architecture**

```mermaid
graph LR
    A[Generate<br/>Code] --> B[Execute<br/>in Sandbox]
    B --> C{Error?}
    C -- No --> D[Return Result]
    C -- Yes --> E[Analyze Error<br/>via LLM]
    E --> F{Max<br/>Attempts?}
    F -- No --> A
    F -- Yes --> G[Return Failure]
```

**Usage**

```python
from core.agents.coding import CodingAgent

agent = CodingAgent(
    max_fix_attempts=5,
    execution_timeout=30,
)

# Auto-debug loop: generates code, runs it, fixes errors automatically
result = await agent.fix_code(
    code="def add(a, b):\n    retun a + b",
    error="SyntaxError: invalid syntax"
)

# Generate unit tests from existing code
tests = await agent.generate_tests(
    code="def add(a, b): return a + b",
    requirements="Test with positive, negative, and zero values"
)

# Explain code
explanation = await agent.explain_code(code)

# Refactor code
refactored = await agent.refactor_code(code, goal="improve readability")
```

### Supported Languages

```python
from core.agents.coding.types import CodeLanguage

CodeLanguage.PYTHON    # "python"
CodeLanguage.JAVASCRIPT # "javascript"
CodeLanguage.BASH       # "bash"
```

**LangChain Tools Integration**

```python
from core.agents.coding_tools import register_coding_tools

tools = register_coding_tools()  # Returns list of LangChain Tool objects
```

---

## Security Notes

!!! warning "Sandbox Required"
    The `CodingAgent` **requires** the Sandbox Service (Docker-based execution). It will refuse to run code locally, preventing arbitrary code execution on the host.

!!! info "Vision Dependency"
    The `BrowserAgent` requires a configured Vision provider (Ollama, OpenAI, or Anthropic). Make sure `VISION_PROVIDER` is set in your `.env`.
