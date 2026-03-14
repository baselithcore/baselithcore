import io
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from core.observability.logging import get_logger
from core.config.plugins import get_plugin_config
from core.marketplace.validator import PluginValidator

logger = get_logger(__name__)


class PluginPublisher:
    """
    Handles packaging and submitting plugins to the central marketplace natively in the core.
    """

    def __init__(self):
        self.config = get_plugin_config()
        self.validator = PluginValidator()

        # Derive base URL by stripping registry.json if present
        url = self.config.registry_url.rstrip("/")
        if url.endswith("/registry.json"):
            url = url[: -len("/registry.json")]
        self.base_url = url

    async def publish(
        self,
        plugin_path: str,
        admin_key: Optional[str] = None,
        auth_token: Optional[str] = None,
        registry_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Validate, package, and submit a plugin to the marketplace.

        Args:
            plugin_path: Path to the local plugin directory.
            admin_key: API Key for authentication (Legacy).
            auth_token: JWT Token for centralized authentication.
            registry_url: Optional override for the marketplace URL.

        Returns:
            Dict[str, Any]: Result of the publication process.
        """
        path = Path(plugin_path)
        if not path.is_dir():
            return {
                "status": "error",
                "message": f"Path is not a directory: {plugin_path}",
            }

        # 1. Validate (synchronous call expecting a Path)
        result = self.validator.validate(path)
        if not result.is_valid:
            errors = [f"{e.level}: {e.message}" for e in result.errors]
            return {
                "status": "error",
                "message": "Validation failed",
                "issues": errors,
            }

        if not result.metadata:
            return {"status": "error", "message": "Could not extract metadata"}

        metadata = result.metadata

        # 2. Package into ZIP
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for file_path in path.rglob("*"):
                if file_path.is_file():
                    # Avoid zipping VCS or temp files
                    if any(part.startswith(".") for part in file_path.parts):
                        continue
                    if "__pycache__" in file_path.parts:
                        continue

                    zip_file.write(file_path, file_path.relative_to(path))

        zip_buffer.seek(0)

        # Use the override URL if provided, otherwise the configured base URL
        base_url = registry_url.rstrip("/") if registry_url else self.base_url

        # 3. Submit
        async with httpx.AsyncClient() as client:
            try:
                # Use metadata.id as the plugin identifier
                plugin_id = metadata.id
                files = {"file": (f"{plugin_id}.zip", zip_buffer, "application/zip")}

                headers = {}
                params = {}

                if auth_token:
                    headers["Authorization"] = f"Bearer {auth_token}"
                elif admin_key:
                    params["admin_key"] = admin_key
                else:
                    return {"status": "error", "message": "No authentication provided"}

                response = await client.post(
                    f"{base_url}/api/marketplace/plugins/submit",
                    files=files,
                    params=params,
                    headers=headers,
                    timeout=60.0,
                )

                if response.status_code == 200:
                    return response.json()
                else:
                    try:
                        error_msg = response.json().get("detail", response.text)
                    except Exception:
                        error_msg = response.text
                    return {
                        "status": "error",
                        "message": f"Server returned {response.status_code}: {error_msg}",
                    }

            except Exception as e:
                logger.error(f"Publication failed: {e}")
                return {"status": "error", "message": f"Connection error: {e}"}
