"""
Code Sandbox integration.

Supports isolated execution of untrusted code snippets.
"""

from .bootstrap import FileTransfer
from .policy import build_sandbox_runtime_kwargs
from .pool import PoolConfig, PooledContainer, SandboxPool
from .resources import Resources
from .service import ExecutionResult, SandboxService
from .streaming import StreamFrame

__all__ = [
    "ExecutionResult",
    "FileTransfer",
    "PoolConfig",
    "PooledContainer",
    "Resources",
    "SandboxPool",
    "SandboxService",
    "StreamFrame",
    "build_sandbox_runtime_kwargs",
]
