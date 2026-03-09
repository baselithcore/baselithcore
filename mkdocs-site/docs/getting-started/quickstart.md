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

!!! tip "Performance Tip: Native Ollama"
    While `ollama` is provided in the Docker stack for convenience (CI/CD, headless Linux), running the **[Ollama Native App](https://ollama.com/)** is significantly faster on macOS (Metal) and Windows/Linux with dedicated GPUs.
    To use a native instance, simply disable the `ollama` service in `docker-compose.yml` and set `LLM_API_BASE=http://host.docker.internal:11434` in your `.env`.
```

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

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello, how do you work?"}'
```

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
│   ├── marketplace/        # Example: marketplace plugin
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

Open your browser at `http://localhost:8000/docs` and test the `/api/chat/stream` endpoint:

1. Click on **POST /api/chat/stream**
2. Click **Try it out**
3. Enter the request body:

   ```json
   {
     "message": "Analyze AI market trends",
     "stream": true
   }
   ```

4. Click **Execute**

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

# View logs for a specific plugin
baselith plugin logs my-plugin --follow

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
# Start Jaeger
docker run -d -p 16686:16686 -p 6831:6831/udp jaegertracing/all-in-one

# Configure in .env
TELEMETRY_OTEL_ENDPOINT=http://localhost:4317
TELEMETRY_ENABLED=true
```

Access the Jaeger UI at `http://localhost:16686`.

---

## 8. Frontend Development (Optional)

If you're developing with the frontend:

```bash
# Navigate to frontend directory
cd frontend

# Install dependencies
npm install

# Start development server
npm run dev
```

The frontend will be available at `http://localhost:5173` (or the port shown in the terminal).

---

## Common Workflows

### Testing Agent Response

```bash
# Using curl
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Explain quantum computing", "session_id": "test-123"}'

# Using Python
python scripts/test_chat.py "Explain quantum computing"
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
