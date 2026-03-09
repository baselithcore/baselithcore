# Reference Product: FAQ Agent

> **Educational Example**: This is a minimal reference product demonstrating the Baselith-Core Framework's architectural patterns.

This example demonstrates the "Strategic Moves" of version 2.0:

1. **Framework Contract**: Full implementation of `AgentLifecycle` including startup/shutdown hooks.
2. **Error Semantics**: Use of standardized `FrameworkErrorCode`.
3. **Deterministic Mode**: Behavior under reproducible conditions.

## Key Features

- **Lifecycle Management**: The agent strictly follows `UNINITIALIZED -> STARTING -> READY -> RUNNING -> STOPPED`.
- **Health Checks**: Implements deep health checks reporting status details.
- **Hooks**: Uses `before_execute` to validate inputs and `on_error` for recovery.

- **Deterministic**: When run with `CORE_DETERMINISTIC_MODE=true`, provides consistent answers suitable for regression testing.

## How to Run

```bash
# Debug run with deterministic mode
export CORE_DETERMINISTIC_MODE=true
python examples/reference-product/demo.py
```

## Structure

- `agent.py`: The reference implementation.
- `plugin.py`: How to wrap the agent in a plugin.
- `demo.py`: Executable script.
