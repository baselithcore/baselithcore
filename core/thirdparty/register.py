"""DORA Register of Information service — Regulation (EU) 2022/2554 Art. 28.

Maintains the register of ICT third-party providers, the business functions they
support, and the contractual arrangements between them, with referential
validation so the register stays internally consistent. Surfaces the
third-party concentration view that backs the Art. 29 risk assessment, and
renders the register in the ESA ITS template layout via
:func:`core.thirdparty.export.build_register`.

The store is a Protocol with an in-memory reference implementation; production
deployments register a durable store. Outbound regulatory submission of the
register remains the operator's action.
"""

from __future__ import annotations

from typing import Any, Protocol

from core.observability.logging import get_logger
from core.thirdparty.export import build_register
from core.thirdparty.types import (
    ContractualArrangement,
    ICTFunction,
    ICTProvider,
    Substitutability,
)

logger = get_logger(__name__)

# Substitutability bands that make a provider a concentration concern when it
# supports a critical or important function (Art. 29 single-point-of-failure).
_CONCENTRATION_RISK_BANDS = frozenset(
    {Substitutability.NOT_SUBSTITUTABLE, Substitutability.HIGHLY_COMPLEX}
)


class RegisterValidationError(Exception):
    """Raised when an arrangement references unknown entities or is inconsistent."""


class RegisterStore(Protocol):
    """Persistence boundary for the register of information."""

    async def save_provider(self, provider: ICTProvider) -> None: ...
    async def get_provider(self, provider_id: str) -> ICTProvider | None: ...
    async def list_providers(self) -> list[ICTProvider]: ...

    async def save_function(self, function: ICTFunction) -> None: ...
    async def get_function(self, function_id: str) -> ICTFunction | None: ...
    async def list_functions(self) -> list[ICTFunction]: ...

    async def save_arrangement(self, arrangement: ContractualArrangement) -> None: ...
    async def get_arrangement(
        self, reference_number: str
    ) -> ContractualArrangement | None: ...
    async def list_arrangements(self) -> list[ContractualArrangement]: ...


class InMemoryRegisterStore:
    """Reference in-memory register store (non-durable; tests/single-process)."""

    def __init__(self) -> None:
        self._providers: dict[str, ICTProvider] = {}
        self._functions: dict[str, ICTFunction] = {}
        self._arrangements: dict[str, ContractualArrangement] = {}

    async def save_provider(self, provider: ICTProvider) -> None:
        self._providers[provider.id] = provider

    async def get_provider(self, provider_id: str) -> ICTProvider | None:
        return self._providers.get(provider_id)

    async def list_providers(self) -> list[ICTProvider]:
        return list(self._providers.values())

    async def save_function(self, function: ICTFunction) -> None:
        self._functions[function.id] = function

    async def get_function(self, function_id: str) -> ICTFunction | None:
        return self._functions.get(function_id)

    async def list_functions(self) -> list[ICTFunction]:
        return list(self._functions.values())

    async def save_arrangement(self, arrangement: ContractualArrangement) -> None:
        self._arrangements[arrangement.reference_number] = arrangement

    async def get_arrangement(
        self, reference_number: str
    ) -> ContractualArrangement | None:
        return self._arrangements.get(reference_number)

    async def list_arrangements(self) -> list[ContractualArrangement]:
        return list(self._arrangements.values())


class RegisterOfInformation:
    """Register of ICT third-party contractual arrangements (DORA Art. 28)."""

    def __init__(self, store: RegisterStore | None = None) -> None:
        self._store = store or InMemoryRegisterStore()

    @property
    def store(self) -> RegisterStore:
        return self._store

    # -- Providers ---------------------------------------------------------

    async def register_provider(self, provider: ICTProvider) -> ICTProvider:
        """Insert or update an ICT third-party provider."""
        if provider.parent_id is not None and provider.parent_id != provider.id:
            if await self._store.get_provider(provider.parent_id) is None:
                raise RegisterValidationError(
                    f"unknown parent provider: {provider.parent_id}"
                )
        await self._store.save_provider(provider)
        logger.info("AUDIT | DORA-REGISTER | provider | id=%s", provider.id)
        return provider

    async def get_provider(self, provider_id: str) -> ICTProvider | None:
        return await self._store.get_provider(provider_id)

    async def list_providers(self) -> list[ICTProvider]:
        return await self._store.list_providers()

    # -- Functions ---------------------------------------------------------

    async def register_function(self, function: ICTFunction) -> ICTFunction:
        """Insert or update a supported business function."""
        await self._store.save_function(function)
        logger.info("AUDIT | DORA-REGISTER | function | id=%s", function.id)
        return function

    async def get_function(self, function_id: str) -> ICTFunction | None:
        return await self._store.get_function(function_id)

    async def list_functions(self) -> list[ICTFunction]:
        return await self._store.list_functions()

    # -- Arrangements ------------------------------------------------------

    async def register_arrangement(
        self, arrangement: ContractualArrangement
    ) -> ContractualArrangement:
        """Insert or update a contractual arrangement after validating its refs.

        Raises:
            RegisterValidationError: if the main provider, any supported
                function, or any subcontractor is unknown, or if the arrangement
                claims to support a critical function without referencing one.
        """
        await self._validate_arrangement(arrangement)
        await self._store.save_arrangement(arrangement)
        logger.info(
            "AUDIT | DORA-REGISTER | arrangement | ref=%s provider=%s critical=%s",
            arrangement.reference_number,
            arrangement.provider_id,
            arrangement.assessment.supports_critical_function,
        )
        return arrangement

    async def _validate_arrangement(self, arrangement: ContractualArrangement) -> None:
        if await self._store.get_provider(arrangement.provider_id) is None:
            raise RegisterValidationError(
                f"unknown provider: {arrangement.provider_id}"
            )
        functions: list[ICTFunction] = []
        for function_id in arrangement.function_ids:
            function = await self._store.get_function(function_id)
            if function is None:
                raise RegisterValidationError(f"unknown function: {function_id}")
            functions.append(function)
        for subcontractor_id in arrangement.subcontractor_ids:
            if await self._store.get_provider(subcontractor_id) is None:
                raise RegisterValidationError(
                    f"unknown subcontractor: {subcontractor_id}"
                )
        if arrangement.assessment.supports_critical_function and not any(
            f.is_critical_or_important for f in functions
        ):
            raise RegisterValidationError(
                "arrangement supports a critical function but references none "
                "with critical/important criticality"
            )

    async def get_arrangement(
        self, reference_number: str
    ) -> ContractualArrangement | None:
        return await self._store.get_arrangement(reference_number)

    async def list_arrangements(self) -> list[ContractualArrangement]:
        return await self._store.list_arrangements()

    async def arrangements_for_provider(
        self, provider_id: str
    ) -> list[ContractualArrangement]:
        """All arrangements whose main provider is ``provider_id``."""
        return [
            a
            for a in await self._store.list_arrangements()
            if a.provider_id == provider_id
        ]

    # -- Risk views --------------------------------------------------------

    async def concentration_summary(self) -> dict[str, Any]:
        """Third-party concentration view backing the Art. 29 risk assessment.

        Flags providers that support a critical/important function under an
        arrangement assessed as hard to substitute — the single-point-of-failure
        concentrations DORA Art. 29 asks the entity to evaluate.
        """
        arrangements = await self._store.list_arrangements()
        per_provider: dict[str, int] = {}
        critical_providers: set[str] = set()
        flags: list[dict[str, str]] = []
        for arr in arrangements:
            per_provider[arr.provider_id] = per_provider.get(arr.provider_id, 0) + 1
            if arr.assessment.supports_critical_function:
                critical_providers.add(arr.provider_id)
                if arr.assessment.substitutability in _CONCENTRATION_RISK_BANDS:
                    flags.append(
                        {
                            "provider_id": arr.provider_id,
                            "reference_number": arr.reference_number,
                            "substitutability": arr.assessment.substitutability.value,
                        }
                    )
        return {
            "providers": len(await self._store.list_providers()),
            "arrangements": len(arrangements),
            "critical_or_important_arrangements": sum(
                1 for a in arrangements if a.assessment.supports_critical_function
            ),
            "providers_supporting_critical": sorted(critical_providers),
            "arrangements_per_provider": per_provider,
            "concentration_flags": flags,
        }

    async def export_register(self) -> dict[str, Any]:
        """Render the register in the ESA ITS template layout."""
        return build_register(
            await self._store.list_providers(),
            await self._store.list_functions(),
            await self._store.list_arrangements(),
        )


_register: RegisterOfInformation | None = None


def get_register() -> RegisterOfInformation:
    """Get or create the global register over an in-memory store."""
    global _register
    if _register is None:
        _register = RegisterOfInformation()
    return _register


__all__ = [
    "InMemoryRegisterStore",
    "RegisterOfInformation",
    "RegisterStore",
    "RegisterValidationError",
    "get_register",
]
