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
# Copy template
cp -r templates/baselith-core-collab my-agent-project
cd my-agent-project

# Install and run
pip install -r requirements.txt
python main.py
```

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
