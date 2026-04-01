"""
Sbx Client.

CLI wrapper for the Docker Sandbox (sbx) tool.
"""

import asyncio
import os
import subprocess
from typing import Optional, List, Dict
from core.observability.logging import get_logger

logger = get_logger(__name__)


class SbxException(Exception):
    """Base class for sbx related errors."""
    pass


class SbxClient:
    """
    Wrapper for 'sbx' CLI tool to manage microVM-based sandboxes.
    """

    def __init__(self, sbx_path: str = "sbx", profile: Optional[str] = None):
        """
        Initialize SbxClient.

        Args:
            sbx_path: Path to the sbx binary.
            profile: Optional sbx profile to use.
        """
        self.sbx_path = sbx_path
        self.profile = profile
        self._version: Optional[str] = None

    async def check_availability(self) -> bool:
        """
        Check if sbx CLI is available and functional.

        Returns:
            bool: True if available, False otherwise.
        """
        try:
            process = await asyncio.create_subprocess_exec(
                self.sbx_path,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await process.communicate()
            if process.returncode == 0:
                self._version = stdout.decode().strip()
                logger.debug(f"Detected sbx version: {self._version}")
                return True
            return False
        except FileNotFoundError:
            return False
        except Exception as e:
            logger.warning(f"Error checking sbx availability: {e}")
            return False

    async def run(
        self,
        command: List[str],
        image: Optional[str] = None,
        envs: Optional[Dict[str, str]] = None,
        mounts: Optional[Dict[str, str]] = None,
        network: bool = False,
        timeout: int = 30,
    ) -> tuple[str, str, int]:
        """
        Run a command in a new sbx sandbox.

        Args:
            command: Command and arguments to run.
            image: Docker image to use (optional).
            envs: Environment variables.
            mounts: host_path:container_path mappings.
            network: Whether to allow network access.
            timeout: Execution timeout in seconds.

        Returns:
            tuple: (stdout, stderr, exit_code)
        """
        args = [self.sbx_path, "run"]
        
        if self.profile:
            args.extend(["--profile", self.profile])

        if not network:
            # sbx by default restricts network, but let's be explicit if needed
            # For now we assume default sbx behavior is restrictive
            pass

        if mounts:
            for host_path, container_path in mounts.items():
                args.extend(["--mount", f"{host_path}:{container_path}"])

        if envs:
            for key, val in envs.items():
                args.extend(["--env", f"{key}={val}"])

        if image:
            args.extend(["--image", image])

        # Final command
        args.extend(["--", *command])

        logger.debug(f"Executing sbx command: {' '.join(args)}")

        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
                return (
                    stdout.decode().strip(),
                    stderr.decode().strip(),
                    process.returncode or 0,
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return ("", "Execution timed out (sbx)", 124)

        except Exception as e:
            logger.error(f"Error running sbx command: {e}")
            return ("", str(e), 1)
