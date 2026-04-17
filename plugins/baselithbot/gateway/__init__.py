"""Remote gateway control surfaces (SSH + Tailscale)."""

from .ssh import SSHGateway, SSHGatewayConfig
from .tailscale import TailscaleGateway, TailscaleStatus

__all__ = [
    "SSHGateway",
    "SSHGatewayConfig",
    "TailscaleGateway",
    "TailscaleStatus",
]
