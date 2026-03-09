# Example Plugin

This is a template/reference plugin demonstrating the "Plugin-First" architecture of the Baselith-Core.

## Structure

The plugin is structured to separate concerns and promote modularity:

- **`plugin.py`**: The main entry point. Defines `ExamplePlugin` which inherits from `AgentPlugin`, `RouterPlugin`, etc. It orchestrates the components.
- **`agent.py`**: Contains the agent logic (`ExampleAgent`). This is where the "brains" of your plugin live.
- **`router.py`**: Contains the API definitions (`create_router`). This is where you define your FastAPI endpoints.
- **`persistence.py`**: Handles database connections and schema management using the core storage configuration.
- **`handlers.py`**: Defines Flow Handlers for executing business logic triggered by specific system intents.
- **`memory.py`**: Integration with the core `AgentMemory` system via `ExampleMemory` class.
- **`models.py`**: Pydantic models for data validation and structure.
- **`utils.py`**: Helper functions and utilities.
- **`static/`**: Directory for frontend assets (JS, CSS) that are injected into the dashboard.

## How to Use This Template

1. **Copy the directory**:

    ```bash
    cp -r plugins/example-plugin plugins/my-new-plugin
    ```

2. **Rename internal references**:
    - Update `metadata` in `plugin.py`:
        - `name`: "my-new-plugin"
        - `description`: Your description
        - `dependencies`: Your dependencies
    - Rename `ExamplePlugin`, `ExampleAgent` classes to match your domain.

3. **Implement Logic**:
    - **Back-end**: Edit `agent.py` to implement your agent's reasoning and tool usage.
    - **API**: Edit `router.py` to expose endpoints.
    - **Front-end**: Add widgets in `static/` and register them in `plugin.py` (`get_scripts`, `get_stylesheets`).

4. **Register**:
    - Ensure your plugin is discovered by the core loader (restarting the core system usually suffices).

## Features Demonstrated

- **Agent Integration**: How to provide an agent to the swarm.
- **API Extension**: How to add custom HTTP endpoints. **Nota**: Gli endpoint sono protetti da autenticazione (`require_roles`) per default nel template.
- **Graph Schema**: How to register new entity and relationship types for the knowledge graph.
- **Intent Handling**: How to register patterns to route natural language user queries to your plugin.
- **Frontend Injection**: How to inject custom JS/CSS into the main dashboard.
- **Persistence**: How to manage database connections and tables safely.
