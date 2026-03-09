"""
Unit tests for the Human-in-the-Loop module (core/human).

Tests cover:
- HumanIntervention class with sync and async callbacks
- All interaction types (APPROVAL, INPUT, SELECTION, NOTIFICATION)
- Error handling in callbacks
- Pending request management
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from core.human import (
    HumanIntervention,
    HumanRequest,
    InteractionType,
    InteractionStatus,
)


# --- FIXTURES ---


@pytest.fixture
def intervention() -> HumanIntervention:
    """Create a basic HumanIntervention instance without callback."""
    return HumanIntervention()


@pytest.fixture
def async_callback() -> AsyncMock:
    """Create an async mock callback."""
    return AsyncMock(return_value=True)


@pytest.fixture
def sync_callback() -> MagicMock:
    """Create a sync mock callback."""
    return MagicMock(return_value=True)


# --- REQUEST APPROVAL TESTS ---


@pytest.mark.asyncio
async def test_request_approval_with_async_callback(async_callback: AsyncMock):
    """Test approval request with async callback returns True."""
    intervention = HumanIntervention(callback=async_callback)

    result = await intervention.request_approval("Deploy to production?")

    assert result is True
    async_callback.assert_called_once()
    call_arg = async_callback.call_args[0][0]
    assert isinstance(call_arg, HumanRequest)
    assert call_arg.type == InteractionType.APPROVAL
    assert call_arg.description == "Deploy to production?"


@pytest.mark.asyncio
async def test_request_approval_with_sync_callback(sync_callback: MagicMock):
    """Test approval request with sync callback returns True."""
    intervention = HumanIntervention(callback=sync_callback)

    result = await intervention.request_approval("Delete files?")

    assert result is True
    sync_callback.assert_called_once()


@pytest.mark.asyncio
async def test_request_approval_rejected():
    """Test approval request returns False when callback returns False."""
    callback = AsyncMock(return_value=False)
    intervention = HumanIntervention(callback=callback)

    result = await intervention.request_approval("Dangerous action?")

    assert result is False


@pytest.mark.asyncio
async def test_request_approval_no_callback(intervention: HumanIntervention):
    """Test approval request returns False when no callback is set."""
    result = await intervention.request_approval("No interface connected")

    assert result is False


@pytest.mark.asyncio
async def test_request_approval_with_context():
    """Test approval request passes context correctly."""
    callback = AsyncMock(return_value=True)
    intervention = HumanIntervention(callback=callback)

    await intervention.request_approval(
        "Send emails?",
        timeout=60,
        context={"recipient_count": 100},
    )

    call_arg = callback.call_args[0][0]
    assert call_arg.timeout_seconds == 60
    assert call_arg.data == {"recipient_count": 100}


# --- ASK INPUT TESTS ---


@pytest.mark.asyncio
async def test_ask_input_returns_string():
    """Test ask_input returns the callback response as string."""
    callback = AsyncMock(return_value="user_input_value")
    intervention = HumanIntervention(callback=callback)

    result = await intervention.ask_input("What is your name?")

    assert result == "user_input_value"
    call_arg = callback.call_args[0][0]
    assert call_arg.type == InteractionType.INPUT


@pytest.mark.asyncio
async def test_ask_input_empty_when_no_callback(intervention: HumanIntervention):
    """Test ask_input returns empty string when no callback."""
    result = await intervention.ask_input("Question?")

    assert result == ""


@pytest.mark.asyncio
async def test_ask_input_with_timeout():
    """Test ask_input passes timeout correctly."""
    callback = AsyncMock(return_value="answer")
    intervention = HumanIntervention(callback=callback)

    await intervention.ask_input("Quick question?", timeout=30)

    call_arg = callback.call_args[0][0]
    assert call_arg.timeout_seconds == 30


# --- REQUEST SELECTION TESTS ---


@pytest.mark.asyncio
async def test_request_selection_valid_option():
    """Test selection returns valid option when selected."""
    callback = AsyncMock(return_value="staging")
    intervention = HumanIntervention(callback=callback)

    result = await intervention.request_selection(
        "Choose environment:",
        options=["staging", "production"],
    )

    assert result == "staging"
    call_arg = callback.call_args[0][0]
    assert call_arg.type == InteractionType.SELECTION
    assert call_arg.options == ["staging", "production"]


@pytest.mark.asyncio
async def test_request_selection_invalid_option():
    """Test selection returns None for invalid option."""
    callback = AsyncMock(return_value="invalid_option")
    intervention = HumanIntervention(callback=callback)

    result = await intervention.request_selection(
        "Choose:",
        options=["a", "b", "c"],
    )

    assert result is None


@pytest.mark.asyncio
async def test_request_selection_no_callback(intervention: HumanIntervention):
    """Test selection returns None when no callback."""
    result = await intervention.request_selection(
        "Choose:",
        options=["x", "y"],
    )

    assert result is None


# --- NOTIFY TESTS ---


@pytest.mark.asyncio
async def test_notify_calls_callback():
    """Test notify calls the callback with NOTIFICATION type."""
    callback = AsyncMock()
    intervention = HumanIntervention(callback=callback)

    await intervention.notify("Task completed", context={"task_id": "123"})

    callback.assert_called_once()
    call_arg = callback.call_args[0][0]
    assert call_arg.type == InteractionType.NOTIFICATION
    assert call_arg.description == "Task completed"
    assert call_arg.data == {"task_id": "123"}


@pytest.mark.asyncio
async def test_notify_no_callback(intervention: HumanIntervention):
    """Test notify works without callback (no exception)."""
    # Should not raise
    await intervention.notify("Info message")


# --- ERROR HANDLING TESTS ---


@pytest.mark.asyncio
async def test_callback_exception_returns_false():
    """Test that callback exception results in False for approval."""
    callback = AsyncMock(side_effect=ValueError("Callback error"))
    intervention = HumanIntervention(callback=callback)

    result = await intervention.request_approval("Will fail?")

    assert result is False


@pytest.mark.asyncio
async def test_callback_exception_returns_empty_string_for_input():
    """Test that callback exception results in empty string for input."""
    callback = AsyncMock(side_effect=RuntimeError("Error"))
    intervention = HumanIntervention(callback=callback)

    result = await intervention.ask_input("Will fail?")

    assert result == ""


# --- PENDING REQUESTS TESTS ---


@pytest.mark.asyncio
async def test_no_pending_requests_after_completion():
    """Test that completed requests are removed from pending."""
    callback = AsyncMock(return_value=True)
    intervention = HumanIntervention(callback=callback)

    await intervention.request_approval("Test")

    assert intervention.has_pending_requests() is False
    assert intervention.get_pending_requests() == []


@pytest.mark.asyncio
async def test_no_pending_requests_after_error():
    """Test that errored requests are also removed from pending."""
    callback = AsyncMock(side_effect=Exception("Error"))
    intervention = HumanIntervention(callback=callback)

    await intervention.request_approval("Test")

    assert intervention.has_pending_requests() is False


# --- REQUEST STATUS TESTS ---


@pytest.mark.asyncio
async def test_request_status_completed():
    """Test request status is COMPLETED after successful callback."""
    last_request = None

    async def capture_callback(request: HumanRequest):
        nonlocal last_request
        last_request = request
        return True

    intervention = HumanIntervention(callback=capture_callback)
    await intervention.request_approval("Test")

    assert last_request is not None
    assert last_request.status == InteractionStatus.COMPLETED
    assert last_request.response is True


@pytest.mark.asyncio
async def test_request_status_rejected_no_callback(intervention: HumanIntervention):
    """Test request status is REJECTED when no callback."""
    # We can't easily capture the request without a callback,
    # but we verify it returns False (rejection behavior)
    result = await intervention.request_approval("No callback")
    assert result is False


# --- INTERACTION TYPE TESTS ---


def test_interaction_type_values():
    """Test all InteractionType enum values exist."""
    assert InteractionType.APPROVAL.value == "approval"
    assert InteractionType.INPUT.value == "input"
    assert InteractionType.SELECTION.value == "selection"
    assert InteractionType.NOTIFICATION.value == "notification"


def test_interaction_status_values():
    """Test all InteractionStatus enum values exist."""
    assert InteractionStatus.PENDING.value == "pending"
    assert InteractionStatus.APPROVED.value == "approved"
    assert InteractionStatus.REJECTED.value == "rejected"
    assert InteractionStatus.COMPLETED.value == "completed"
    assert InteractionStatus.TIMEOUT.value == "timeout"


# --- HUMAN REQUEST DATACLASS TESTS ---


def test_human_request_defaults():
    """Test HumanRequest dataclass has correct defaults."""
    request = HumanRequest(
        type=InteractionType.APPROVAL,
        description="Test request",
    )

    assert request.type == InteractionType.APPROVAL
    assert request.description == "Test request"
    assert request.id is not None
    assert request.data == {}
    assert request.options is None
    assert request.timeout_seconds is None
    assert request.status == InteractionStatus.PENDING
    assert request.response is None
    assert request.created_at is not None
