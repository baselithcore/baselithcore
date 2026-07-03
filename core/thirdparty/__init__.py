"""DORA Register of Information subsystem (Regulation (EU) 2022/2554 Art. 28).

A structured register of ICT third-party providers, the business functions they
support, and the contractual arrangements between them — with referential
validation, a concentration view backing the Art. 29 risk assessment, and an
export in the ESA ITS template layout (Commission Implementing Regulation (EU)
2024/2956). Domain-agnostic infrastructure, so it lives in the Sacred Core;
storage-agnostic via the ``RegisterStore`` Protocol.
"""

from core.thirdparty.export import REGISTER_STANDARD, build_register
from core.thirdparty.register import (
    InMemoryRegisterStore,
    RegisterOfInformation,
    RegisterStore,
    RegisterValidationError,
    get_register,
)
from core.thirdparty.types import (
    ContractualArrangement,
    DataSensitivity,
    FunctionCriticality,
    ICTFunction,
    ICTProvider,
    ProviderType,
    ServiceAssessment,
    Substitutability,
)

__all__ = [
    # Types
    "ProviderType",
    "FunctionCriticality",
    "Substitutability",
    "DataSensitivity",
    "ICTProvider",
    "ICTFunction",
    "ServiceAssessment",
    "ContractualArrangement",
    # Register
    "RegisterValidationError",
    "RegisterStore",
    "InMemoryRegisterStore",
    "RegisterOfInformation",
    "get_register",
    # Export
    "REGISTER_STANDARD",
    "build_register",
]
