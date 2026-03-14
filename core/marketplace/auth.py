import asyncio
import json
import os
from pathlib import Path
from typing import Optional, Dict, Any

import httpx
from core.observability.logging import get_logger
from core.config.plugins import get_plugin_config

logger = get_logger(__name__)


class CredentialsManager:
    """
    Manages secure storage and retrieval of credentials for the CLI.
    """

    def __init__(self, directory: Optional[Path] = None):
        if directory:
            self.credentials_dir = directory
        else:
            self.credentials_dir = Path.home() / ".baselith"
        self.credentials_file = self.credentials_dir / "credentials.json"

    def _ensure_dir(self) -> None:
        """Ensure the credentials directory exists with appropriate permissions."""
        if not self.credentials_dir.exists():
            # Create directory with 0o700 permissions (rwx for owner only)
            self.credentials_dir.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(self.credentials_dir, 0o700)
            except OSError as e:
                logger.warning(
                    f"Could not change permissions on {self.credentials_dir}: {e}"
                )

    async def save_api_key(self, api_key: str) -> None:
        """
        Save the API key securely.
        """

        def _sync_save():
            self._ensure_dir()
            data = self._load_data_sync()
            data["api_key"] = api_key

            with open(self.credentials_file, "w") as f:
                json.dump(data, f)

            try:
                # Enforce 0o600 permissions (rw for owner only)
                os.chmod(self.credentials_file, 0o600)
            except OSError as e:
                logger.warning(
                    f"Could not change permissions on {self.credentials_file}: {e}"
                )

        await asyncio.to_thread(_sync_save)

    async def load_api_key(self) -> Optional[str]:
        """
        Retrieve the saved API key.
        """
        data = await self._load_data()
        return data.get("api_key")

    async def save_token(
        self, token: str, user_data: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Save a centralized Authentication Token (e.g. JWT) and optional user context.
        """

        def _sync_save():
            self._ensure_dir()
            data = self._load_data_sync()
            data["auth_token"] = token
            if user_data:
                data["user"] = user_data

            with open(self.credentials_file, "w") as f:
                json.dump(data, f)

            try:
                os.chmod(self.credentials_file, 0o600)
            except OSError:
                pass

        await asyncio.to_thread(_sync_save)

    async def load_token(self) -> Optional[str]:
        """
        Retrieve the saved Authentication Token.
        """
        data = await self._load_data()
        return data.get("auth_token")

    async def delete_credentials(self) -> None:
        """
        Delete all saved credentials (API key, auth token, user profile).
        """

        def _sync_delete():
            if self.credentials_file.exists():
                try:
                    self.credentials_file.unlink()
                except OSError as e:
                    logger.warning(f"Could not delete credentials file: {e}")

        await asyncio.to_thread(_sync_delete)

    async def delete_api_key(self) -> None:
        """
        Delete the saved API key.
        """

        def _sync_delete():
            data = self._load_data_sync()
            if "api_key" in data:
                del data["api_key"]
                with open(self.credentials_file, "w") as f:
                    json.dump(data, f)
                try:
                    os.chmod(self.credentials_file, 0o600)
                except OSError:
                    pass

        await asyncio.to_thread(_sync_delete)

    async def verify_token(
        self, token: str, auth_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Verify a JWT token with the remote Identity Provider.

        Args:
            token: JWT token to verify.
            auth_url: Optional override for the IdP URL.

        Returns:
            Dict[str, Any]: User profile or error information.
        """
        config = get_plugin_config()

        # Determine IdP URL
        target_url = auth_url or config.auth_url
        if not target_url:
            # Fallback: derive from registry URL
            registry_url = config.registry_url.rstrip("/")
            if registry_url.endswith("/registry.json"):
                target_url = registry_url[: -len("/registry.json")]
            else:
                target_url = registry_url

        target_url = target_url.rstrip("/")

        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{target_url}/api/auth/verify",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10.0,
                )

                if response.status_code == 200:
                    return {
                        "status": "success",
                        "user": response.json(),
                    }
                else:
                    return {
                        "status": "error",
                        "message": f"Server returned {response.status_code}",
                        "detail": response.text,
                    }
            except Exception as e:
                logger.error(f"Auth verification failed: {e}")
                return {
                    "status": "error",
                    "message": f"Connection error: {e}",
                }

    async def _load_data(self) -> Dict[str, Any]:
        """Async wrapper for loading credentials data."""
        return await asyncio.to_thread(self._load_data_sync)

    def _load_data_sync(self) -> Dict[str, Any]:
        """Load the JSON credentials data, returning an empty dict if not found."""
        if not self.credentials_file.exists():
            return {}
        try:
            with open(self.credentials_file, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to read credentials file: {e}")
            return {}


class AuthService:
    """
    High-level service for handling synchronization with remote Identity Providers.
    """

    def __init__(self):
        self.manager = CredentialsManager()

    async def get_current_identity(
        self, auth_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Retrieves the identity of the currently logged-in user by verifying their token.
        """
        token = await self.manager.load_token()
        if not token:
            return {"status": "error", "message": "Not logged in"}

        return await self.manager.verify_token(token, auth_url=auth_url)

    async def sync_user_profile(self, auth_url: Optional[str] = None) -> bool:
        """
        Verifies the current token and updates the local user profile cache.
        """
        result = await self.get_current_identity(auth_url=auth_url)
        if result["status"] == "success":
            token = await self.manager.load_token()
            if token:
                await self.manager.save_token(token, user_data=result["user"])
                return True
        return False
