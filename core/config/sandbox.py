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
    enable_network: bool = Field(default=False, description="Enable network in sandbox")
    docker_socket: str = Field(
        default="/var/run/docker.sock", description="Docker socket path"
    )
    sbx_path: str = Field(default="sbx", description="Path to the sbx CLI binary")
    sbx_profile: str | None = Field(
        default=None, description="Optional profile to use with sbx"
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
