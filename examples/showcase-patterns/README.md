# Showcase Patterns Example

Complete demonstration of all Agentic Design Patterns.

## Patterns Demonstrated

### Core Patterns

- ✅ **Reflection**: Agent self-evaluation and improvement
- ✅ **Tool Use**: External tool integration
- ✅ **Planning**: Task decomposition and scheduling
- ✅ **Baselith-Core**: Agent collaboration

### Memory Patterns

- ✅ **Short-term Memory**: Conversation context
- ✅ **Long-term Memory**: Persistent knowledge via vector store
- ✅ **Episodic Memory**: Experience recall

### Human Interaction

- ✅ **Human-in-the-Loop**: Approval workflows
- ✅ **Active Learning**: Feedback collection

## Quick Start

```bash
cd examples/showcase-patterns
pip install -r requirements.txt
python main.py
```

Then visit: `http://localhost:8000/docs` for interactive API docs.

## API Endpoints

| Endpoint            | Description                       |
| ------------------- | --------------------------------- |
| `POST /reflect`     | Demonstrate reflection pattern    |
| `POST /plan`        | Demonstrate planning pattern      |
| `POST /remember`    | Demonstrate memory patterns       |
| `POST /collaborate` | Demonstrate baselith-core pattern |
| `POST /approve`     | Demonstrate human-in-the-loop     |
