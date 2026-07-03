import io
import json
import zipfile
from pathlib import Path
from typing import Any

import httpx
import yaml

from core.config.plugins import get_plugin_config
from core.marketplace.validator import PluginValidator
from core.observability.logging import get_logger

logger = get_logger(__name__)
_HTTP_CLIENT: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    """Reuse a single AsyncClient to preserve connection pooling."""
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        _HTTP_CLIENT = httpx.AsyncClient(
            timeout=60.0,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _HTTP_CLIENT


def _inject_integrity(manifest_path: Path, integrity_hash: str) -> bytes | None:
    """Return manifest bytes with ``integrity_sha256`` injected.

    Parses the manifest (YAML or JSON), sets the ``integrity_sha256`` field,
    and re-serializes. Returns ``None`` if parsing fails — the caller falls
    back to shipping the original manifest unchanged.
    """
    try:
        raw = manifest_path.read_text(encoding="utf-8")
        if manifest_path.suffix == ".json":
            data = json.loads(raw) or {}
            data["integrity_sha256"] = integrity_hash
            return json.dumps(data, indent=2).encode("utf-8")
        data = yaml.safe_load(raw) or {}
        data["integrity_sha256"] = integrity_hash
        return yaml.safe_dump(data, sort_keys=False).encode("utf-8")
    except Exception as exc:
        logger.warning(
            "Failed to inject integrity hash into %s: %s",
            manifest_path.name,
            type(exc).__name__,
        )
        return None


class PluginPublisher:
    """
    Handles packaging and submitting plugins to the central marketplace natively in the core.
    """

    def __init__(self):
        self.config = get_plugin_config()
        self.validator = PluginValidator()

        # For publishing, we STRICTLY use the official marketplace URL
        # to prevent redirection to rogue registries via environment variables.
        self.base_url = self.config.OFFICIAL_MARKETPLACE_URL

    async def publish(
        self,
        plugin_path: str,
        admin_key: str | None = None,
        auth_token: str | None = None,
        registry_url: str | None = None,
    ) -> dict[str, Any]:
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
        #
        # Exclusions mirror `[tool.setuptools.exclude-package-data]` in each
        # plugin's `pyproject.toml`: dev-only trees (node_modules, UI sources,
        # linter caches, local state) must not ship in the distributed
        # archive — they bloat the upload past the marketplace 20MB gate and
        # are not needed at runtime (the plugin consumes only `ui/dist/`).
        excluded_dir_parts = {
            "__pycache__",
            "node_modules",
            ".ruff_cache",
            ".mypy_cache",
            ".pytest_cache",
            ".state",
            "build",
            "dist",
            ".egg-info",
        }
        excluded_ui_src_prefix = ("ui", "src")

        # Compute integrity hash over the executable surface BEFORE zipping
        # so we can inject it into the manifest that ships in the archive.
        # Imported lazily to avoid a circular import via core.plugins.exporters.
        from core.plugins.integrity import compute_plugin_hash

        integrity_hash = compute_plugin_hash(path)
        manifest_filenames = {"manifest.yaml", "manifest.yml", "manifest.json"}

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for file_path in path.rglob("*"):
                if not file_path.is_file():
                    continue
                parts = file_path.relative_to(path).parts
                # Skip dotfiles / dotdirs (VCS, caches, env files).
                if any(p.startswith(".") for p in parts):
                    continue
                # Skip known-bloat directories anywhere in the tree.
                if any(p in excluded_dir_parts for p in parts):
                    # But explicitly allow ui/dist as it contains the compiled frontend
                    if len(parts) >= 2 and parts[0] == "ui" and parts[1] == "dist":
                        pass
                    else:
                        continue
                # Skip *.egg-info directories (suffix match, not exact).
                if any(p.endswith(".egg-info") for p in parts):
                    continue
                # Skip UI sources — only compiled `ui/dist/` ships.
                if (
                    len(parts) >= 2
                    and parts[0] == excluded_ui_src_prefix[0]
                    and parts[1] == excluded_ui_src_prefix[1]
                ):
                    continue
                # Skip compiled Python bytecode.
                if file_path.suffix in {".pyc", ".pyo"}:
                    continue

                # Inject integrity_sha256 into the top-level manifest on the fly.
                if len(parts) == 1 and file_path.name in manifest_filenames:
                    rewritten = _inject_integrity(file_path, integrity_hash)
                    if rewritten is not None:
                        zip_file.writestr(str(file_path.relative_to(path)), rewritten)
                        continue

                zip_file.write(file_path, file_path.relative_to(path))

        zip_buffer.seek(0)

        # NOTE: For security and consistency, we ALWAYS publish to the official marketplace.
        # The registry_url override is ignored for the submission endpoint.
        base_url = self.base_url

        # 3. Submit
        client = _get_http_client()
        try:
            # Use metadata.id as the plugin identifier
            plugin_id = metadata.id
            files = {
                "file": (f"{plugin_id}.zip", zip_buffer.getvalue(), "application/zip")
            }

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
