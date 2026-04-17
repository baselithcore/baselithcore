"""Model failover + auth profile rotation."""

from .auth_rotation import AuthProfile, AuthProfilePool
from .failover import FailoverPolicy, ProviderConfig, ProviderError
from .router import ModelRouter

__all__ = [
    "AuthProfile",
    "AuthProfilePool",
    "FailoverPolicy",
    "ProviderConfig",
    "ProviderError",
    "ModelRouter",
]
