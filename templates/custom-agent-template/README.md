# Custom Agent Template

Template for creating custom agents with tools, prompts, and memory.

## Quick Start

```bash
# 1. Copy the template
cp -r templates/custom-agent-template plugins/my-agent

# 2. Customize
cd plugins/my-agent
# Edit agent.py, tools.py, and prompts/
```

## Structure

```txt
custom-agent-template/
├── agent.py         # Agent implementation
├── tools.py         # Tool definitions
├── prompts/
│   └── system.md    # System prompt
└── tests/
    └── test_agent.py
```

## Agent Implementation

```python
from core.agents.base import BaseAgent

class MyAgent(BaseAgent):
    name = "my-agent"
    
    def __init__(self, llm_service, tools=None):
        super().__init__(llm_service)
        self.tools = tools or []
    
    async def process(self, message: str, context: dict) -> str:
        # 1. Prepare prompt
        # 2. Call LLM
        # 3. Process response
        return response
```

## Tools (Functions)

Define functions that the agent can invoke:

```python
from core.agents.tools import tool

@tool
def search_database(query: str) -> list[dict]:
    """Search in the internal database."""
    # Implementation
    return results

@tool
def send_email(to: str, subject: str, body: str) -> bool:
    """Send an email."""
    # Implementation
    return True
```

## Prompts

Use Markdown files for prompts:

```markdown
# prompts/system.md

You are an assistant specialized in {{DOMAIN}}.

## Competencies
- Answer questions about {{TOPIC}}
- Search for information in the database

## Instructions
1. Analyze the question
2. If necessary, use available tools
3. Answer clearly
```

## Registration

Create a plugin wrapper:

```python
# plugin.py
from core.plugins import AgentPlugin
from .agent import MyAgent

class MyAgentPlugin(AgentPlugin):
    name = "my-agent"
    
    def create_agent(self, service, **kwargs):
        from .tools import search_database, send_email
        return MyAgent(service, tools=[search_database, send_email])

plugin = MyAgentPlugin()
```
