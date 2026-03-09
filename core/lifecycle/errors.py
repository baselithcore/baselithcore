"""
Standardized error semantics for the framework.

Defines a hierarchy of errors and standard error codes to ensure
consistent error handling and reporting across the system.
"""

from enum import Enum
from typing import Any, Dict, Optional


class FrameworkErrorCode(str, Enum):
    """Standard error codes for the framework."""

    # Lifecycle Errors (000-099)
    LIFECYCLE_START_FAILED = "FW-001"
    LIFECYCLE_SHUTDOWN_TIMEOUT = "FW-002"
    LIFECYCLE_STATE_INVALID = "FW-003"
    LIFECYCLE_HEALTH_CHECK_FAILED = "FW-004"

    # Hook Errors (100-199)
    HOOK_EXECUTION_FAILED = "FW-101"
    HOOK_REGISTRATION_FAILED = "FW-102"

    # Agent Runtime Errors (200-299)
    AGENT_NOT_READY = "FW-201"
    AGENT_EXECUTION_TIMEOUT = "FW-202"
    AGENT_CONTEXT_INVALID = "FW-203"
    AGENT_RESOURCE_EXHAUSTED = "FW-204"

    # General Failures (900-999)
    RECOVERABLE_FAILURE = "FW-900"
    FATAL_FAILURE = "FW-999"


class BaseFrameworkError(Exception):
    """Base exception for all framework-related errors."""

    def __init__(
        self,
        message: str,
        code: FrameworkErrorCode,
        context: Optional[Dict[str, Any]] = None,
        recoverable: bool = False,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.context = context or {}
        self.recoverable = recoverable

    def to_dict(self) -> Dict[str, Any]:
        """Serialize error for logging/API."""
        return {
            "error": self.__class__.__name__,
            "message": self.message,
            "code": self.code.value,
            "recoverable": self.recoverable,
            "context": self.context,
        }


class LifecycleError(BaseFrameworkError):
    """Errors related to component lifecycle management."""

    pass


class AgentError(BaseFrameworkError):
    """Errors occurring during agent execution."""

    pass


class RecoverableError(BaseFrameworkError):
    """
    An error that the system expects and can potentially recover from
    (e.g., temporary service unavailability, rate limits).
    """

    def __init__(
        self,
        message: str,
        code: FrameworkErrorCode = FrameworkErrorCode.RECOVERABLE_FAILURE,
        context: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message, code, context, recoverable=True)


class FatalError(BaseFrameworkError):
    """
    Critical error requiring system intervention or shutdown.
    Cannot be automatically recovered.
    """

    def __init__(
        self,
        message: str,
        code: FrameworkErrorCode = FrameworkErrorCode.FATAL_FAILURE,
        context: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message, code, context, recoverable=False)
