import json
import os
from pathlib import Path
from typing import Optional, Dict, Any

from core.observability.logging import get_logger

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

    def save_api_key(self, api_key: str) -> None:
        """
        Save the API key securely.
        """
        self._ensure_dir()
        data = self._load_data()
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

    def load_api_key(self) -> Optional[str]:
        """
        Retrieve the saved API key.
        """
        data = self._load_data()
        return data.get("api_key")

    def save_token(
        self, token: str, user_data: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Save a centralized Authentication Token (e.g. JWT) and optional user context.
        """
        self._ensure_dir()
        data = self._load_data()
        data["auth_token"] = token
        if user_data:
            data["user"] = user_data

        with open(self.credentials_file, "w") as f:
            json.dump(data, f)

        try:
            os.chmod(self.credentials_file, 0o600)
        except OSError:
            pass

    def load_token(self) -> Optional[str]:
        """
        Retrieve the saved Authentication Token.
        """
        data = self._load_data()
        return data.get("auth_token")

    def delete_credentials(self) -> None:
        """
        Delete all saved credentials (API key, auth token, user profile).
        """
        if self.credentials_file.exists():
            try:
                self.credentials_file.unlink()
            except OSError as e:
                logger.warning(f"Could not delete credentials file: {e}")

    def delete_api_key(self) -> None:
        """
        Delete the saved API key.
        """
        data = self._load_data()
        if "api_key" in data:
            del data["api_key"]
            with open(self.credentials_file, "w") as f:
                json.dump(data, f)
            try:
                os.chmod(self.credentials_file, 0o600)
            except OSError:
                pass

    def _load_data(self) -> Dict[str, Any]:
        """Load the JSON credentials data, returning an empty dict if not found."""
        if not self.credentials_file.exists():
            return {}
        try:
            with open(self.credentials_file, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to read credentials file: {e}")
            return {}
