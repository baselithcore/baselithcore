"""Model failover + auth profile rotation."""

from plugins.baselithbot.model_routing.auth_rotation import AuthProfile, AuthProfilePool
from plugins.baselithbot.model_routing.failover import FailoverPolicy, ProviderConfig, ProviderError
from plugins.baselithbot.model_routing.router import ModelRouter

__all__ = [
    "AuthProfile",
    "AuthProfilePool",
    "FailoverPolicy",
    "ProviderConfig",
    "ProviderError",
    "ModelRouter",
]
