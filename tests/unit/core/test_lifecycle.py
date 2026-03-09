import pytest
import asyncio
import os
from unittest.mock import patch

from core.lifecycle import (
    AgentState,
    HealthStatus,
    AgentHooks,
    LifecycleMixin,
    LifecycleError,
    FrameworkErrorCode,
    BaseFrameworkError,
)
from core.lifecycle.deterministic import (
    apply_deterministic_mode,
    get_llm_override_kwargs,
)
from core.config import get_core_config

# --- Protocols & Models Tests ---


def test_agent_state_enum():
    """Verify AgentState enum values."""
    assert AgentState.READY == "ready"
    assert AgentState.ERROR == "error"
    assert AgentState.UNINITIALIZED == "uninitialized"


def test_health_status_model():
    """Verify HealthStatus pydantic model."""
    status = HealthStatus(
        is_healthy=True, status=AgentState.READY, details={"load": 0.5}
    )
    assert status.is_healthy is True
    assert status.status == AgentState.READY
    assert status.details["load"] == 0.5
    assert status.error is None


def test_agent_hooks_model():
    """Verify AgentHooks pydantic model."""
    hooks = AgentHooks()
    assert isinstance(hooks.before_execute, list)
    assert isinstance(hooks.on_state_change, list)
    assert len(hooks.before_execute) == 0


# --- LifecycleMixin Tests ---


class MockAgent(LifecycleMixin):
    """Minimal agent for testing LifecycleMixin."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.startup_called = False
        self.shutdown_called = False
        self.reset_called = False
        self.health_check_data = {"test": "ok"}

    async def _do_startup(self) -> None:
        self.startup_called = True
        await asyncio.sleep(0.01)

    async def _do_shutdown(self) -> None:
        self.shutdown_called = True

    async def _do_reset(self) -> None:
        self.reset_called = True

    async def _do_health_check(self):
        return self.health_check_data


@pytest.mark.asyncio
async def test_lifecycle_mixin_success_flow():
    """Test standard startup -> ready -> stop flow."""
    agent = MockAgent()
    assert agent.state == AgentState.UNINITIALIZED

    # Startup
    await agent.startup()
    assert agent.state == AgentState.READY
    assert agent.startup_called is True

    # Health check
    health = await agent.health_check()
    assert health.is_healthy is True
    assert health.status == AgentState.READY
    assert health.details == {"test": "ok"}

    # Reset
    await agent.reset()
    assert agent.reset_called is True

    # Shutdown
    await agent.shutdown()
    assert agent.state == AgentState.STOPPED
    assert agent.shutdown_called is True


@pytest.mark.asyncio
async def test_lifecycle_mixin_error_flow():
    """Test startup failure transitions to ERROR state."""

    class ErrorAgent(LifecycleMixin):
        async def _do_startup(self):
            raise ValueError("Startup failed")

    agent = ErrorAgent()
    with pytest.raises(LifecycleError) as excinfo:
        await agent.startup()

    assert agent.state == AgentState.ERROR
    assert excinfo.value.code == FrameworkErrorCode.LIFECYCLE_START_FAILED

    # Health check in error state
    health = await agent.health_check()
    assert health.is_healthy is False
    assert health.status == AgentState.ERROR
    assert "in ERROR state" in health.error


@pytest.mark.asyncio
async def test_lifecycle_mixin_pause_resume():
    """Test pause and resume transitions."""
    agent = MockAgent()
    await agent.startup()

    # Transition to RUNNING (manual since Mixin doesn't auto-transition to RUNNING)
    await agent._transition_state(AgentState.RUNNING)
    assert agent.state == AgentState.RUNNING

    await agent.pause()
    assert agent.state == AgentState.PAUSED

    await agent.resume()
    assert agent.state == AgentState.RUNNING


@pytest.mark.asyncio
async def test_lifecycle_hooks():
    """Test hook execution."""
    agent = MockAgent()

    sync_hook_called = False
    async_hook_called = False

    def sync_hook(old, new):
        nonlocal sync_hook_called
        sync_hook_called = True

    async def async_hook(old, new):
        nonlocal async_hook_called
        async_hook_called = True

    agent.hooks.on_state_change.append(sync_hook)
    agent.hooks.on_state_change.append(async_hook)

    await agent.startup()

    assert sync_hook_called is True
    assert async_hook_called is True


# --- Errors Tests ---


def test_base_framework_error_serialization():
    """Verify error serialization to dict."""
    err = BaseFrameworkError(
        message="Test error",
        code=FrameworkErrorCode.LIFECYCLE_START_FAILED,
        context={"id": 123},
        recoverable=True,
    )
    d = err.to_dict()
    assert d["message"] == "Test error"
    assert d["code"] == "FW-001"
    assert d["recoverable"] is True
    assert d["context"] == {"id": 123}


# --- Deterministic Tests ---


def test_deterministic_mode_logic():
    """Verify deterministic mode helpers."""
    config = get_core_config()
    original_mode = config.deterministic_mode
    original_seed = config.random_seed

    try:
        config.deterministic_mode = True
        config.random_seed = 999

        with patch("random.seed") as mock_seed:
            apply_deterministic_mode()
            mock_seed.assert_called_with(999)
            assert os.environ["PYTHONHASHSEED"] == "999"

        overrides = get_llm_override_kwargs()
        assert overrides["temperature"] == 0.0
        assert overrides["seed"] == 999
        assert overrides["top_p"] == 1.0

    finally:
        config.deterministic_mode = original_mode
        config.random_seed = original_seed
