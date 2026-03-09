from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class SandboxConfig(BaseSettings):
    """
    Sandbox service configuration.
    """

    model_config = SettingsConfigDict(
        env_prefix="SANDBOX_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    image: str = Field(
        default="python:3.11-slim", description="Docker image for sandbox"
    )
    timeout: int = Field(default=30, description="Execution timeout in seconds")
    enable_network: bool = Field(default=False, description="Enable network in sandbox")
    docker_socket: str = Field(
        default="/var/run/docker.sock", description="Docker socket path"
    )


# Global instance
_sandbox_config: Optional[SandboxConfig] = None


def get_sandbox_config() -> SandboxConfig:
    """Get or create the global sandbox configuration instance."""
    global _sandbox_config
    if _sandbox_config is None:
        _sandbox_config = SandboxConfig()
    return _sandbox_config
