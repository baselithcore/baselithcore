"""
Sbx Factory.

Handles sbx client lifecycle and sandbox environment management.
"""

from typing import Optional
from core.observability.logging import get_logger
from .sbx_client import SbxClient

logger = get_logger(__name__)


class SbxFactory:
    """
    Factory for managing sbx client and environment parameters.
    """

    def __init__(self, sbx_path: str = "sbx", profile: Optional[str] = None):
        """
        Initialize SbxFactory.

        Args:
            sbx_path: Path to the sbx binary.
            profile: Optional sbx profile to use.
        """
        self.sbx_path = sbx_path
        self.profile = profile
        self._client: Optional[SbxClient] = None

    @property
    def client(self) -> SbxClient:
        """
        Lazily initialize and return the Sbx client.

        Returns:
            SbxClient: The initialized Sbx client.
        """
        if self._client is None:
            self._client = SbxClient(sbx_path=self.sbx_path, profile=self.profile)
        return self._client

    async def ensure_available(self) -> None:
        """
        Verify the existence of the sbx binary and ensure it is functional.

        Raises:
            RuntimeError: If sbx is not found or not functional.
        """
        if not await self.client.check_availability():
            logger.error(f"sbx CLI not found at {self.sbx_path} or not functional.")
            raise RuntimeError(
                f"sbx CLI not found at {self.sbx_path}. "
                "Please install it via 'brew install docker/tap/sbx' on macOS."
            )
        logger.info("sbx CLI found and functional.")
