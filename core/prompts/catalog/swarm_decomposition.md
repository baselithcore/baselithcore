---
name: swarm_decomposition
version: "1"
labels: [production]
description: Task-decomposition prompt for dynamic swarm agent generation.
variables: [query]
---
Analyze the following complex request and:

1. Decompose it into 2-4 independent sub-tasks.
2. For each sub-task, define a specialized virtual agent role.

Request: {{ query }}

Respond with a JSON array of objects:
[
    {
        "description": "detailed task description",
        "capability": "research|analysis|synthesis|validation",
        "agent_name": "Specialized Name",
        "agent_role": "brief_role_identifier",
        "agent_prompt": "Specific system instructions for this agent"
    },
    ...
]
