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

## Coverage Requirements

BaselithCore maintains strict test coverage standards to ensure production reliability and stability.

### Coverage Targets by Module Type

| Module Type | Minimum | Target | Notes |
|-------------|---------|--------|-------|
| **Infrastructure** (cache, db, config) | 60% | 75% | Critical path coverage required |
| **Orchestration** (workflows, reasoning) | 55% | 70% | Core logic + edge cases |
| **Utilities** (tokens, similarity) | 70% | 90% | Pure functions, easy to test |
| **Plugins** | 50% | 65% | Domain-specific, may use external services |

### Current Coverage Status

Check real-time coverage: [Coverage Report](../htmlcov/index.html)

Run coverage locally:

```bash
pytest --cov=core --cov=plugins --cov-report=html
open htmlcov/index.html
```

### Coverage Improvement Roadmap

**v0.3.0 Status**: 31.56% overall
**v0.3.5 Target**: 54% minimum
**v0.4.0 Target**: 66%+ overall

#### Priority Modules for Improvement

The following modules are prioritized for coverage improvement in upcoming releases:

1. **`core/utils/tokens.py`** (0% → 85%)
   - Token estimation utilities
   - CJK/code/prose text classification
   - Tiktoken integration and fallback

2. **`core/workflows/executor.py`** (26% → 55%)
   - Workflow orchestration
   - Node execution and state management
   - Error handling and timeouts

3. **`core/world_model/`** (20% avg → 70%)
   - MCTS simulation and planning
   - State prediction and rollback
   - Risk assessment and safety checks

4. **`core/task_queue/`** (40% avg → 65%)
   - Job scheduling and monitoring
   - Status tracking and progress updates
   - Queue management and retry logic

---

## Testing Best Practices

### Mocking LLM Services

Always mock LLM services in tests to avoid external dependencies and ensure test reliability.

#### Auto-Mocking via Conftest

The global conftest automatically mocks LLM services for all tests:

```python title="tests/conftest.py"
@pytest.fixture(autouse=True)
def mock_llm_service():
    """Auto-mock LLM service for all tests."""
    with patch("core.services.llm.get_llm_service") as mock:
        mock_service = MagicMock()
        mock_service.generate_response.return_value = "Test response"
        mock.return_value = mock_service
        yield mock_service
```

#### Custom LLM Mocking

For tests requiring specific LLM behavior:

```python
@pytest.fixture
def mock_llm_custom():
    """Custom LLM mock with specific response."""
    with patch("core.services.llm.get_llm_service") as mock:
        mock_service = MagicMock()
        # Simulate structured JSON response
        mock_service.generate_response.return_value = '{"status": "success", "data": "test"}'
        mock.return_value = mock_service
        yield mock_service

@pytest.mark.asyncio
async def test_llm_integration(mock_llm_custom):
    """Test component using LLM service."""
    from core.reasoning import ReasoningEngine

    engine = ReasoningEngine()
    result = await engine.reason("test query")

    assert result is not None
    mock_llm_custom.generate_response.assert_called_once()
```

### Async Testing

Use `@pytest.mark.asyncio` for all async tests:

```python
# ✅ Correct async testing
@pytest.mark.asyncio
async def test_async_function():
    """Test async operation properly."""
    result = await my_async_function()
    assert result.success

# ❌ Incorrect - don't use asyncio.run
def test_async_wrong():
    result = asyncio.run(my_async_function())  # NO! Use async def
```

#### Async Fixtures

```python
@pytest.fixture
async def async_setup():
    """Async fixture for test setup."""
    manager = await AsyncManager.create()
    yield manager
    await manager.cleanup()

@pytest.mark.asyncio
async def test_with_async_fixture(async_setup):
    """Test using async fixture."""
    result = await async_setup.process()
    assert result is not None
```

### Redis and RQ Mocking

Follow the established pattern for mocking Redis and RQ job queues:

```python title="tests/unit/core/task_queue/test_scheduler.py"
@pytest.fixture
def mock_queue():
    """Mock RQ Queue."""
    queue = MagicMock()
    job = MagicMock(spec=Job)
    job.id = "test-job-id"
    job.get_status.return_value = JobStatus.QUEUED

    queue.enqueue.return_value = job
    queue.enqueue_at.return_value = job
    queue.enqueue_in.return_value = job

    return queue

@pytest.fixture
def mock_get_queue(mock_queue):
    """Mock get_queue function."""
    with patch("core.task_queue.scheduler.get_queue", return_value=mock_queue):
        yield mock_queue

def test_schedule_task(mock_get_queue):
    """Test task scheduling."""
    from core.task_queue import schedule_task

    job_id = schedule_task("my_task", arg1="value1")

    assert job_id == "test-job-id"
    mock_get_queue.enqueue.assert_called_once()
```

#### Mocking Redis Client

```python
@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    with patch("redis.Redis") as mock:
        client = MagicMock()
        client.get.return_value = b'{"key": "value"}'
        client.set.return_value = True
        client.exists.return_value = 1
        mock.from_url.return_value = client
        yield client

def test_cache_operations(mock_redis):
    """Test cache with mocked Redis."""
    from core.cache import get_cache

    cache = get_cache()
    cache.set("key", "value")

    mock_redis.set.assert_called_once()
```

### Configuration Mocking

Mock configuration to control test environment:

```python
@pytest.fixture
def temp_storage_config():
    """Temporary storage configuration."""
    from core.config.storage import StorageConfig

    config = StorageConfig(
        cache_redis_url="redis://localhost:6379/15",  # Test DB
        vector_db_path=":memory:",  # In-memory
        session_ttl=3600
    )

    with patch("core.config.storage.get_storage_config", return_value=config):
        yield config

def test_with_custom_config(temp_storage_config):
    """Test using temporary configuration."""
    from core.cache import get_cache

    cache = get_cache()  # Uses test config
    assert cache.config.cache_redis_url.endswith("/15")
```

### Test Organization

Organize tests by functionality using classes:

```python
class TestRiskAssessor:
    """Tests for risk assessment functionality."""

    @pytest.fixture
    def assessor(self):
        """Risk assessor instance."""
        return RiskAssessor()

    def test_assess_low_risk_action(self, assessor):
        """Test low-risk action assessment."""
        action = Action(name="query", action_type=ActionType.QUERY)
        result = assessor.assess_action(action)
        assert result["level"] == RiskLevel.LOW

    def test_assess_high_risk_action(self, assessor):
        """Test high-risk action assessment."""
        action = Action(name="delete", action_type=ActionType.DELETE)
        result = assessor.assess_action(action)
        assert result["level"] in [RiskLevel.HIGH, RiskLevel.CRITICAL]
```

### Parameterized Tests

Use `@pytest.mark.parametrize` for testing multiple scenarios:

```python
@pytest.mark.parametrize("input_text,expected_type", [
    ("Hello world", "prose"),
    ("def foo(): pass", "code"),
    ("你好世界", "cjk"),
    ("", "prose"),
])
def test_text_classification(input_text, expected_type):
    """Test text classification with various inputs."""
    from core.utils.tokens import _classify_text

    result = _classify_text(input_text)
    assert result == expected_type
```

### Edge Case Testing

Always test edge cases and boundary conditions:

```python
class TestTokenEstimation:
    """Tests for token estimation."""

    def test_empty_string(self):
        """Empty string should return 0 tokens."""
        assert estimate_tokens("") == 0

    def test_very_long_text(self):
        """Very long text should be handled efficiently."""
        text = "word " * 10000  # 10k words
        tokens = estimate_tokens(text)
        assert tokens > 0
        assert tokens < len(text)  # Tokens < characters

    def test_unicode_handling(self):
        """Unicode characters should be counted correctly."""
        text = "Hello 🌍 世界"
        tokens = estimate_tokens(text)
        assert tokens > 0
```

---

## General Best Practices

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

### Async Best Practices

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
