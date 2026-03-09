---
title: Testing
description: Complete guide to testing BaselithCore and its plugins
---



Complete guide to testing BaselithCore.

---

## Test Structure

```text
tests/
├── unit/                  # Unit tests
│   ├── core/
│   │   ├── test_di.py
│   │   ├── test_orchestration.py
│   │   └── ...
│   └── plugins/
│       └── test_my_plugin.py
├── integration/           # Integration tests
│   ├── test_plugin_loading.py
│   └── test_flow_execution.py
├── e2e/                   # End-to-end tests
│   └── test_chat_workflow.py
└── conftest.py            # Shared fixtures
```

---

## Setup

### Dependencies

```bash
pip install pytest pytest-asyncio pytest-cov httpx
```

### Configuration

```python title="pytest.ini"
[pytest]
asyncio_mode = auto
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
```

---

## Unit Tests

### Core Services

```python title="tests/unit/core/test_memory.py"
import pytest
from core.memory import MemoryManager

@pytest.fixture
async def memory_manager():
    manager = MemoryManager()
    yield manager
    await manager.dispose()

class TestMemoryManager:
    async def test_add_message(self, memory_manager):
        await memory_manager.add_message(
            session_id="test-session",
            role="user",
            content="Test message"
        )
        
        context = await memory_manager.get_context("test-session")
        assert len(context.messages) == 1
        assert context.messages[0].content == "Test message"
    
    async def test_context_retrieval(self, memory_manager):
        # Setup
        await memory_manager.add_message(
            session_id="test-session",
            role="user",
            content="Message 1"
        )
        
        # Test
        context = await memory_manager.get_context("test-session")
        assert context.session_id == "test-session"
```

### Plugin Tests

```python title="tests/unit/plugins/test_my_plugin.py"
import pytest
from plugins.my_plugin.plugin import MyPlugin
from plugins.my_plugin.handlers import MySyncHandler

@pytest.fixture
def mock_plugin():
    plugin = MyPlugin()
    plugin.config = {"api_key": "test-key"}
    return plugin

class TestMyPlugin:
    async def test_handler_execution(self, mock_plugin):
        handler = MySyncHandler(mock_plugin)
        result = await handler.handle("test query", {})
        
        assert result is not None
        assert isinstance(result, str)
    
    def test_plugin_metadata(self, mock_plugin):
        metadata = mock_plugin.metadata
        assert metadata["name"] == "my-plugin"
        assert metadata["version"] is not None
```

---

## Integration Tests

### Plugin Loading

```python title="tests/integration/test_plugin_loading.py"
import pytest
from core.plugins import get_plugin_registry, PluginLoader

class TestPluginIntegration:
    async def test_load_and_register_plugin(self):
        loader = PluginLoader(plugins_dir="plugins/")
        plugin = await loader.load("my-plugin")
        
        assert plugin is not None
        
        registry = get_plugin_registry()
        assert registry.is_registered("my-plugin")
    
    async def test_handler_resolution(self):
        registry = get_plugin_registry()
        handler = registry.get_handler("my_intent")
        
        assert handler is not None
```

### Flow Execution

```python title="tests/integration/test_flow_execution.py"
import pytest
from core.orchestration import Orchestrator

class TestFlowExecution:
    async def test_end_to_end_flow(self):
        orchestrator = Orchestrator()
        
        response = await orchestrator.handle_request(
            query="test query",
            session_id="test-session",
            stream=False
        )
        
        assert response is not None
        assert isinstance(response, str)
```

---

## End-to-End Tests

### Chat Workflow

```python title="tests/e2e/test_chat_workflow.py"
import pytest
from httpx import AsyncClient

class TestChatAPI:
    @pytest.fixture
    async def client(self):
        from backend import app
        async with AsyncClient(app=app, base_url="http://test") as ac:
            yield ac
    
    async def test_chat_endpoint(self, client):
        response = await client.post(
            "/api/chat",
            json={"message": "Hello", "session_id": "test"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "response" in data
        assert "session_id" in data
```

---

## Common Fixtures

```python title="tests/conftest.py"
import pytest
from core.di import Container
from core.interfaces import LLMServiceProtocol

# Mock LLM Service
class MockLLMService:
    async def generate(self, prompt: str, **kwargs):
        return MockResponse(text=f"Mock response to: {prompt}")
    
    async def stream(self, prompt: str, **kwargs):
        yield "Mock "
        yield "streaming "
        yield "response"

class MockResponse:
    def __init__(self, text: str):
        self.text = text
        self.usage = {"prompt_tokens": 10, "completion_tokens": 20}

@pytest.fixture
def mock_container():
    """DI container with mock services."""
    container = Container()
    container.register(LLMServiceProtocol, instance=MockLLMService())
    return container

@pytest.fixture
async def test_db():
    """Test database."""
    # Setup: create test DB
    from core.db import create_tables
    await create_tables()
    
    yield
    
    # Teardown: clean DB
    from core.db import drop_tables
    await drop_tables()
```

---

## Coverage

### Run with Coverage

```bash
# Test with coverage
pytest tests/ --cov=core --cov=plugins --cov-report=html

# Only unit tests
pytest tests/unit/ --cov=core --cov-report=term

# Minimum coverage
pytest tests/ --cov=core --cov-fail-under=80
```

### Report

```bash
# HTML Report
open htmlcov/index.html

# Terminal Report
pytest tests/ --cov=core --cov-report=term-missing
```

---

## Best Practices

### Isolation

```python
# ✅ Each test is isolated
@pytest.fixture
async def isolated_memory():
    manager = MemoryManager()
    yield manager
    await manager.clear_all()  # Cleanup

# ❌ Shared state between tests
global_manager = MemoryManager()  # NO!
```

### Async Testing

```python
# ✅ Use pytest-asyncio
async def test_async_operation():
    result = await async_function()
    assert result is not None

# ❌ Blocking in async test
def test_blocking():
    result = asyncio.run(async_function())  # NO! Use async def
```

### Mock External Services

```python
# ✅ Mock external services
@pytest.fixture
def mock_http_client(monkeypatch):
    async def mock_get(*args, **kwargs):
        return MockResponse(status_code=200, json={"data": "test"})
    
    monkeypatch.setattr("httpx.AsyncClient.get", mock_get)

# ❌ Real calls to external services
async def test_real_api():
    response = await httpx.get("https://api.example.com")  # NO! Flaky
```

---

## CI/CD Integration

### GitHub Actions

```yaml title=".github/workflows/test.yml"
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    
    steps:
      - uses: actions/checkout@v2
      
      - name: Set up Python
        uses: actions/setup-python@v2
        with:
          python-version: '3.10'
      
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install pytest pytest-cov
      
      - name: Run tests
        run: pytest tests/ --cov=core --cov-fail-under=80
      
      - name: Upload coverage
        uses: codecov/codecov-action@v2
```

---

## Debugging Tests

```bash
# Run with verbose output
pytest tests/ -v

# Stop at first failure
pytest tests/ -x

# Run specific test
pytest tests/unit/core/test_memory.py::TestMemoryManager::test_add_message

# Show print statements
pytest tests/ -s
```
