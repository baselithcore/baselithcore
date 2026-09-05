---
title: Quick Start
description: Launch the system and start developing in minutes
---

This guide helps you launch BaselithCore in minutes.

!!! note "Prerequisite"
    Ensure you've completed the [installation](installation.md) before proceeding.

---

## 1. System Launch

### Development Mode

```bash
# Start the development server
baselith run
```

The system will start with a **Premium Startup Dashboard** showing host, port, active workers, and direct links to API documentation.

- **API**: `http://localhost:8000`
- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`

### Docker Compose

```bash
# Start the entire stack (backend + Redis + PostgreSQL + Qdrant + Ollama)
docker compose up -d

# View logs
docker compose logs -f api
```

!!! tip "Performance Tip: Native Ollama"
    While `ollama` is provided in the Docker stack for convenience (CI/CD, headless Linux), running the **[Ollama Native App](https://ollama.com/)** is significantly faster on macOS (Metal) and Windows/Linux with dedicated GPUs.
    To use a native instance, simply disable the `ollama` service in `docker-compose.yml` and set `LLM_API_BASE=http://host.docker.internal:11434` in your `.env`.

---

## 2. Verify Functionality

### Test API Health

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{
  "status": "ok"
}
```

### Test Chat Endpoint

The chat routes (`POST /chat` and `POST /chat/stream`) require authentication. Supply
a valid token (see the [auth configuration](../core-modules/config.md)) via the
`Authorization` header:

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $BASELITH_TOKEN" \
  -d '{"query": "Hello, how do you work?"}'
```

!!! note "Request schema"
    The chat request model uses `query` (the user message) and the optional
    `conversation_id`. Unknown fields are rejected (`extra="forbid"`).

### First Agent, First Crew (library API)

You don't need the server to build agents — the typed library surface works
in any script:

```python
from pydantic import BaseModel
from core.agent import Agent, Crew, Task

class CityInfo(BaseModel):
    city: str
    population: int

async def lookup_population(city: str) -> str:
    """Look up a city's population."""
    ...

# A typed single agent — tools and output schema inferred, output validated.
agent = Agent(output_type=CityInfo, tools=[lookup_population])
result = await agent.run("Tell me about Rome")
result.output  # -> CityInfo (validated, auto-retried on schema failure)

# A collaborative crew — sequential by default, prior outputs feed later tasks.
researcher = Agent(system_prompt="You are a meticulous researcher.")
writer = Agent(system_prompt="You write crisp executive summaries.")
crew = Crew(
    agents=[researcher, writer],
    tasks=[
        Task("Research {topic} and list the key facts.", agent=researcher),
        Task("Write a summary from the research.", agent=writer),
    ],
)
summary = (await crew.run(inputs={"topic": "vector databases"})).final
```

See the [Agent API reference](../core-modules/agent.md) for tools, structured
output, streaming, and crew processes.

---

## 3. Project Structure

```text
baselith-core/
├── core/                   # Framework core (DO NOT modify)
│   ├── agents/             # Base agents
│   ├── config/             # Centralized configuration
│   ├── memory/             # Memory system
│   ├── orchestration/      # Main orchestrator
│   ├── plugins/            # Plugin system
│   ├── resilience/         # Circuit breaker, retry, etc.
│   ├── services/           # Core services (LLM, VectorStore)
│   └── ...
├── plugins/                # Your plugins (extend here)
│   ├── example-plugin/     # Reference plugin structure
│   └── ...
├── configs/                # Configuration files
│   └── plugins.yaml        # Plugin configuration
├── backend.py              # FastAPI entry point
└── .env                    # Environment variables
```

!!! tip "System Overview"
    Run `baselith info` to get a structured overview of your current workspace and environment.

!!! warning "Core Modification"
    The `core/` directory contains framework infrastructure. **Never modify core files directly.** All customization should be done through plugins.

---

## 4. Useful CLI Commands

The framework provides a comprehensive CLI:

### Plugin Management

```bash
# List loaded plugins with readiness status
baselith plugin list

# Create new plugin (supports --interactive wizard)
baselith plugin create my-plugin --type agent

# State-of-the-art status and diagnostics
baselith plugin status
baselith plugin deps check my-plugin
baselith plugin tree
```

### Diagnostics

```bash
# Check system health & connectivity
baselith doctor

# Verify installation integrity
baselith verify

# Show active configuration dashboard
baselith config show

# Cache statistics
baselith cache stats
```

### Development

```bash
# Initialize new project (supports interactive mode)
baselith init my-project --template rag-system

# Start server with reload
baselith run --reload

# Run tests with coverage
baselith test

# Generate API documentation
baselith docs generate
```

---

## 5. Interactive Test

Open your browser at `http://localhost:8000/docs` and test the `/chat/stream` endpoint:

1. Click on **POST /chat/stream**
2. Authorize first (the endpoint requires authentication)
3. Click **Try it out**
4. Enter the request body:

   ```json
   {
     "query": "Analyze AI market trends",
     "stream": true
   }
   ```

5. Click **Execute**

You'll see the streaming response from BaselithCore.

---

## 6. Plugin Configuration

Plugins are configured in `configs/plugins.yaml`:

```yaml title="configs/plugins.yaml"
# Reasoning Agent Plugin - Tree of Thoughts reasoning
reasoning_agent:
  enabled: false
  max_steps: 5
  branching_factor: 3

# Goals Plugin - Long term goals and tracking
goals:
  enabled: false

# Official Marketplace Plugin
marketplace:
  enabled: true
```

After making changes, restart the server to apply them.

---

## 7. Logging and Debugging

### View Logs

BaselithCore unifies all system and library logs. During development, logs are beautifully rendered to the console with colors and rich tracebacks.

```bash
# View real-time system logs
baselith run

# Show the last 100 entries for a plugin from the logs/ directory (optionally by level)
baselith plugin logs my-plugin --lines 100 --level ERROR

# Filter logs by level or keyword
baselith run | grep "ERROR"
```

### Debug Mode

```env title=".env"
# Enable high-fidelity development logs
CORE_DEBUG=true
CORE_LOG_LEVEL=DEBUG
CORE_LOG_FORMAT=text  # Use 'json' for production-style parsing
```

### Tracing with Jaeger

```bash
# Start Jaeger (plus Prometheus and Grafana). Publishes the OTLP gRPC
# collector on 127.0.0.1:4317 and the Jaeger UI on 127.0.0.1:16686.
docker compose -f docker-compose.observability.yml up -d
```

Then point the exporter at the collector in `.env`:

```env title=".env"
TELEMETRY_ENABLED=true
TELEMETRY_OTEL_ENDPOINT=http://localhost:4317
```

Access the Jaeger UI at `http://localhost:16686`.

!!! note "gRPC, not HTTP"
    The exporter speaks OTLP over gRPC, so `TELEMETRY_OTEL_ENDPOINT` must target port `4317` (the default), not the `4318` HTTP port. If you run Jaeger by hand instead of the compose file, publish that port: `docker run -d -e COLLECTOR_OTLP_ENABLED=true -p 16686:16686 -p 4317:4317 jaegertracing/all-in-one`.

---

## 8. Admin Console (Optional)

The framework serves its own web console — chat, health probes, and webhook management — at `http://localhost:8000/console`. It is a dependency-free single-page app bundled with the backend, so there is nothing to install or build. See the [Admin Console](console.md) guide.

---

## Common Workflows

### Testing Agent Response

```bash
# Using curl (chat routes require authentication)
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $BASELITH_TOKEN" \
  -d '{"query": "Explain quantum computing", "conversation_id": "test-123"}'
```

### Monitoring System Metrics

```bash
# View plugin statistics
baselith plugin status

# Cache usage
baselith cache stats

# Queue statistics
baselith queue status
```

---

## Next Steps

<div class="feature-grid" markdown>

<div class="feature-card" markdown>

### :material-puzzle-plus: Create a Plugin

Follow the tutorial to [create your first plugin](first-plugin.md).

</div>

<div class="feature-card" markdown>

### :material-sitemap: Architecture

Learn about the [system architecture](../architecture/overview.md).

</div>

<div class="feature-card" markdown>

### :material-cog: Configuration

Explore [configuration options](../core-modules/config.md) for advanced customization.

</div>

</div>
