# Baselith-Core Collaboration Template

A collaborative baselith-core demonstrating agent orchestration patterns.

## Features

- **Agent Roles**: Researcher, Writer, Reviewer, Orchestrator
- **Agentic Patterns**: Planning, Reflection, Tool Use, Memory
- **Workflow**: Sequential and parallel task execution
- **Human-in-the-Loop**: Approval checkpoints

## Architecture

```text
                    ┌─────────────────┐
                    │  Orchestrator   │
                    │     Agent       │
                    └────────┬────────┘
                             │
           ┌─────────────────┼─────────────────┐
           │                 │                 │
    ┌──────▼──────┐   ┌──────▼──────┐   ┌──────▼──────┐
    │  Researcher │   │   Writer    │   │  Reviewer   │
    │    Agent    │   │   Agent     │   │   Agent     │
    └─────────────┘   └─────────────┘   └─────────────┘
```

## Workflow

1. **Research Phase**: Researcher gathers information from tools/sources
2. **Draft Phase**: Writer creates content based on research
3. **Review Phase**: Reviewer evaluates and provides feedback
4. **Iteration**: Orchestrator manages feedback loops
5. **Approval**: Human-in-the-loop for final approval

## Quick Start

```bash
baselith init my-agent-project --template multi-agent-collab
cd my-agent-project
pip install -r requirements.txt
python main.py          # API on http://127.0.0.1:8000 — try /health and /agents
```

`baselith init` generated `.env` for local development: `APP_ENV=development`,
a random `SECRET_KEY` for this project only (file mode 0600, ignored by git),
loopback-only `HOST`/`PORT` (which `main.py` binds) and `LLM_PROVIDER=ollama`,
the provider that needs no API key.

## Agent Configuration

Edit `config.yaml` to customize agents:

```yaml
agents:
  researcher:
    role: "Research Specialist"
    tools: [web_search, document_retrieval]
    
  writer:
    role: "Content Writer"
    tools: [text_generation]
    
  reviewer:
    role: "Quality Reviewer"
    tools: [analysis, feedback]
```
