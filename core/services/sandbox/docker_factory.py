"""
Docker Factory.

Handles container client lifecycle and sandbox image management.
"""

import asyncio
from pathlib import Path
from typing import Any, TypeAlias

from core.config.sandbox import get_sandbox_config
from core.observability.logging import get_logger

try:
    import docker
    from docker.errors import DockerException

    DockerClient: TypeAlias = docker.DockerClient
except ImportError:
    docker = None  # type: ignore
    DockerException = Exception
    DockerClient: TypeAlias = Any  # type: ignore

logger = get_logger(__name__)

#: The hardened sandbox image's build recipe, shipped alongside this module.
SANDBOX_DOCKERFILE = Path(__file__).parent / "Dockerfile.sandbox"


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
        self._client: DockerClient | None = None

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

    async def _fallback_image(self) -> str:
        """Resolve the base image to use when ``Dockerfile.sandbox`` is absent.

        There is no safe implicit answer here, so there is no implicit answer:
        the sandbox runs untrusted code and the bundled Dockerfile is what
        makes its image hardened. Silently pulling a floating public tag
        instead swapped an audited image for one nobody had reviewed, at a
        reference that can change under the same name.

        An operator who genuinely wants a different base says so twice — by
        enabling ``SANDBOX_ALLOW_UNHARDENED_BASE`` and by naming the image with
        a digest, so what runs is what was reviewed.

        Returns:
            The digest-pinned image reference to pull.

        Raises:
            RuntimeError: The opt-in is off, or on but names no image.
        """
        config = get_sandbox_config()
        if not config.allow_unhardened_base:
            raise RuntimeError(
                f"Sandbox image {self.base_image!r} is missing and "
                f"{SANDBOX_DOCKERFILE} does not exist, so the hardened image "
                "cannot be built. Restore the Dockerfile, or set "
                "SANDBOX_ALLOW_UNHARDENED_BASE=true together with a "
                "digest-pinned SANDBOX_UNHARDENED_BASE_IMAGE to run untrusted "
                "code on an unhardened base."
            )
        if not config.unhardened_base_image:
            raise RuntimeError(
                "SANDBOX_ALLOW_UNHARDENED_BASE is set but "
                "SANDBOX_UNHARDENED_BASE_IMAGE is empty. The fallback image "
                "must be pinned by digest (e.g. 'python@sha256:<64 hex>'); a "
                "floating tag can change between review and execution."
            )
        return config.unhardened_base_image

    async def ensure_image(self) -> None:
        """
        Verify the existence of the sandbox image, building it if missing.

        Builds ``agent-sandbox:latest`` from the bundled ``Dockerfile.sandbox``.
        If that file is absent the call **fails closed** unless an operator has
        opted into a digest-pinned fallback (see :meth:`_fallback_image`).

        Raises:
            RuntimeError: The hardened image cannot be built and no pinned
                fallback was configured.
            Exception: If image acquisition or build otherwise fails.
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
            if not SANDBOX_DOCKERFILE.exists():
                fallback = await self._fallback_image()
                logger.warning(
                    "sandbox_unhardened_base_image_in_use",
                    dockerfile=str(SANDBOX_DOCKERFILE),
                    image=fallback,
                )
                self.base_image = fallback
                await loop.run_in_executor(
                    None, lambda: self.client.images.pull(self.base_image)
                )
                return

            # Build image
            try:
                # We need to set the build context to the directory containing the Dockerfile
                build_context = str(SANDBOX_DOCKERFILE.parent)

                def _build():
                    """Internal image build operation."""
                    image, logs = self.client.images.build(
                        path=build_context,
                        dockerfile=SANDBOX_DOCKERFILE.name,
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
