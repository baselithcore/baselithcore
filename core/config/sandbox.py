from typing import Literal, TypeAlias

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SandboxConfig(BaseSettings):
    """
    Sandbox service configuration.
    """

    model_config = SettingsConfigDict(
        env_prefix="SANDBOX_",
        case_sensitive=False,
        extra="ignore",
    )

    provider: Literal["docker", "sbx"] = Field(
        default="docker", description="Sandbox provider (docker or sbx)"
    )
    image: str = Field(
        default="python:3.12-slim", description="Docker image for sandbox"
    )
    timeout: int = Field(default=30, description="Execution timeout in seconds")
    cost_per_compute_second: float = Field(
        default=0.0,
        description="USD charged per wall-clock compute second of sandbox "
        "execution. 0 (default) keeps cost_usd at 0 while compute_seconds "
        "is still recorded.",
    )
    enable_network: bool = Field(default=False, description="Enable network in sandbox")
    docker_socket: str = Field(
        default="/var/run/docker.sock", description="Docker socket path"
    )
    sbx_path: str = Field(default="sbx", description="Path to the sbx CLI binary")
    sbx_profile: str | None = Field(
        default=None, description="Optional profile to use with sbx"
    )

    # == Pre-execution static analysis (second defense layer) ==
    # The container policy (no network, dropped caps) is the primary layer;
    # AST analysis rejects code before it ever reaches the container.
    static_analysis: bool = Field(
        default=True,
        description="AST-analyze Python code before sandbox execution: "
        "syntax errors are always rejected; flagged imports are handled "
        "per static_analysis_mode.",
    )
    static_analysis_mode: Literal["warn", "block"] = Field(
        default="warn",
        description="'warn' logs flagged imports and proceeds (default); "
        "'block' rejects the execution outright.",
    )
    static_analysis_denied_imports: str = Field(
        default="ctypes,socket,subprocess",
        description="Comma-separated module names flagged by the analyzer.",
    )


# Type aliases
SandboxProvider: TypeAlias = Literal["docker", "sbx"]

# Global instance
_sandbox_config: SandboxConfig | None = None


def get_sandbox_config() -> SandboxConfig:
    """Get or create the global sandbox configuration instance."""
    global _sandbox_config
    if _sandbox_config is None:
        _sandbox_config = SandboxConfig()
    return _sandbox_config
