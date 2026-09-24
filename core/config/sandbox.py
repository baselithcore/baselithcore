"""Sandbox configuration (``SANDBOX_``).

Isolation backend and resource ceilings for the untrusted code the coding
agents execute.
"""

from typing import Literal, TypeAlias

from pydantic import Field, model_validator
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
    enable_network: bool = Field(
        default=False,
        description="Give Docker sandbox containers the default bridge network "
        "(egress for untrusted code). Off: network_mode none.",
    )
    docker_socket: str = Field(
        default="/var/run/docker.sock",
        description="Docker daemon socket for the sandbox client. Used only when "
        "set explicitly and DOCKER_HOST is unset; otherwise Docker's own client "
        "environment decides.",
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

    # == Base-image resolution (fails closed) ==
    # The sandbox image is built from the bundled Dockerfile.sandbox, which is
    # the hardened one: non-root user, no build tooling left behind. When that
    # file is missing the factory used to silently pull a floating
    # ``python:3.12-slim`` instead — an unpinned, unhardened image substituted
    # for the hardened one, with nothing but a log line to say so. It now
    # refuses unless an operator opts in, and the opt-in has to name a digest:
    # a tag is mutable, so "the image we reviewed" and "the image we run" are
    # only the same thing when the reference is content-addressed.
    allow_unhardened_base: bool = Field(
        default=False,
        description="Permit falling back to an external base image when the "
        "bundled core/services/sandbox/Dockerfile.sandbox is missing. "
        "Requires unhardened_base_image.",
    )
    unhardened_base_image: str | None = Field(
        default=None,
        description="Digest-pinned fallback image (e.g. "
        "'python@sha256:<64 hex>'), used only when allow_unhardened_base is "
        "true. Tags are rejected.",
    )

    @model_validator(mode="after")
    def _validate_unhardened_base(self) -> "SandboxConfig":
        """Reject a fallback image that is not pinned to a digest.

        Validated here rather than at the call site so a bad value fails at
        startup, not on the first sandbox execution.

        Raises:
            ValueError: ``unhardened_base_image`` is set but carries no
                ``@sha256:`` digest.
        """
        image = self.unhardened_base_image
        if image is not None and "@sha256:" not in image:
            raise ValueError(
                "SANDBOX_UNHARDENED_BASE_IMAGE must be pinned by digest "
                f"(e.g. 'python@sha256:<64 hex>'), got {image!r}"
            )
        return self


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
