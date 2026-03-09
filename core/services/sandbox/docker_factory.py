"""
Docker Factory.

Handles container client lifecycle and sandbox image management.
"""

from core.observability.logging import get_logger
import asyncio
from typing import Optional, Any, TypeAlias

try:
    import docker
    from docker.errors import DockerException

    DockerClient: TypeAlias = docker.DockerClient
except ImportError:
    docker = None  # type: ignore
    DockerException = Exception
    DockerClient: TypeAlias = Any  # type: ignore

logger = get_logger(__name__)


class DockerFactory:
    """
    Factory for managing Docker client and images.
    """

    def __init__(self, base_image: str = "agent-sandbox:latest"):
        """
        Initialize DockerFactory.

        Args:
            base_image: Name/tag of the image to use for sandboxes.
        """
        self.base_image = base_image
        self._client: Optional[DockerClient] = None

    @property
    def client(self) -> DockerClient:
        """
        Lazily initialize and return the Docker client.

        Connects to the local Docker daemon using environment variables.

        Returns:
            DockerClient: The initialized Docker client.

        Raises:
            DockerException: If connection to Docker daemon fails.
        """
        if self._client is None:
            try:
                self._client = docker.from_env()
            except DockerException as e:
                logger.error(f"Failed to initialize Docker client: {e}")
                raise
        return self._client

    async def ensure_image(self) -> None:
        """
        Verify the existence of the sandbox image, building or pulling it if missing.

        Attempts to build 'agent-sandbox:latest' from Dockerfile.sandbox.
        Falls back to 'python:3.11-slim' if the custom Dockerfile is not found.

        Raises:
            Exception: If image acquisition or build fails.
        """
        loop = asyncio.get_running_loop()

        try:
            await loop.run_in_executor(
                None, lambda: self.client.images.get(self.base_image)
            )
        except DockerException:
            logger.info(
                f"Image {self.base_image} not found. Building from Dockerfile.sandbox..."
            )
            import os

            dockerfile_path = os.path.join(
                os.path.dirname(__file__), "Dockerfile.sandbox"
            )
            if not os.path.exists(dockerfile_path):
                # Fallback to pulling python:3.11-slim if custom dockerfile missing
                logger.warning(
                    f"Dockerfile.sandbox not found at {dockerfile_path}. Pulling python:3.11-slim instead."
                )
                self.base_image = "python:3.11-slim"
                await loop.run_in_executor(
                    None, lambda: self.client.images.pull(self.base_image)
                )
                return

            # Build image
            try:
                # We need to set the build context to the directory containing the Dockerfile
                build_context = os.path.dirname(dockerfile_path)

                def _build():
                    """Internal image build operation."""
                    image, logs = self.client.images.build(
                        path=build_context,
                        dockerfile="Dockerfile.sandbox",
                        tag="agent-sandbox:latest",
                        rm=True,
                    )
                    return logs

                logs = await loop.run_in_executor(None, _build)

                for chunk in logs:
                    if "stream" in chunk:
                        logger.debug(chunk["stream"].strip())

                self.base_image = "agent-sandbox:latest"
                logger.info("Successfully built agent-sandbox:latest")
            except Exception as e:
                logger.error(f"Failed to build sandbox image: {e}")
                raise
