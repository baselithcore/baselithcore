"""Tests for the DORA Art. 28 Register of Information (core/thirdparty)."""

from datetime import date

import pytest

from core.thirdparty import (
    ContractualArrangement,
    FunctionCriticality,
    ICTFunction,
    ICTProvider,
    RegisterOfInformation,
    RegisterValidationError,
    ServiceAssessment,
    Substitutability,
)


def _register() -> RegisterOfInformation:
    return RegisterOfInformation()


async def _provider(reg: RegisterOfInformation, name: str = "AcmeCloud") -> ICTProvider:
    return await reg.register_provider(
        ICTProvider(name=name, country="IE", lei="X" * 20)
    )


async def _critical_function(reg: RegisterOfInformation) -> ICTFunction:
    return await reg.register_function(
        ICTFunction(name="Settlement", criticality=FunctionCriticality.CRITICAL)
    )


class TestProviders:
    async def test_register_and_get(self):
        reg = _register()
        p = await _provider(reg)
        assert (await reg.get_provider(p.id)) is p
        assert [x.id for x in await reg.list_providers()] == [p.id]

    async def test_unknown_parent_rejected(self):
        reg = _register()
        with pytest.raises(RegisterValidationError):
            await reg.register_provider(ICTProvider(name="Sub", parent_id="ghost"))

    async def test_known_parent_accepted(self):
        reg = _register()
        parent = await _provider(reg, "Group")
        child = await reg.register_provider(
            ICTProvider(name="Sub", parent_id=parent.id)
        )
        assert child.parent_id == parent.id


class TestFunctions:
    async def test_register_and_criticality(self):
        reg = _register()
        f = await _critical_function(reg)
        assert f.is_critical_or_important is True
        important = ICTFunction(
            name="Reporting", criticality=FunctionCriticality.IMPORTANT
        )
        assert important.is_critical_or_important is True
        plain = ICTFunction(name="Blog")
        assert plain.is_critical_or_important is False


class TestArrangements:
    async def test_register_valid(self):
        reg = _register()
        p = await _provider(reg)
        f = await _critical_function(reg)
        arr = await reg.register_arrangement(
            ContractualArrangement(
                reference_number="C-1",
                provider_id=p.id,
                function_ids=[f.id],
                start_date=date(2026, 1, 1),
                assessment=ServiceAssessment(supports_critical_function=True),
            )
        )
        assert (await reg.get_arrangement("C-1")) is arr

    async def test_unknown_provider_rejected(self):
        reg = _register()
        with pytest.raises(RegisterValidationError):
            await reg.register_arrangement(
                ContractualArrangement(reference_number="C-1", provider_id="ghost")
            )

    async def test_unknown_function_rejected(self):
        reg = _register()
        p = await _provider(reg)
        with pytest.raises(RegisterValidationError):
            await reg.register_arrangement(
                ContractualArrangement(
                    reference_number="C-1", provider_id=p.id, function_ids=["ghost"]
                )
            )

    async def test_unknown_subcontractor_rejected(self):
        reg = _register()
        p = await _provider(reg)
        with pytest.raises(RegisterValidationError):
            await reg.register_arrangement(
                ContractualArrangement(
                    reference_number="C-1",
                    provider_id=p.id,
                    subcontractor_ids=["ghost"],
                )
            )

    async def test_critical_without_critical_function_rejected(self):
        reg = _register()
        p = await _provider(reg)
        f = await reg.register_function(ICTFunction(name="Minor"))  # not critical
        with pytest.raises(RegisterValidationError):
            await reg.register_arrangement(
                ContractualArrangement(
                    reference_number="C-1",
                    provider_id=p.id,
                    function_ids=[f.id],
                    assessment=ServiceAssessment(supports_critical_function=True),
                )
            )

    async def test_arrangements_for_provider(self):
        reg = _register()
        p = await _provider(reg)
        await reg.register_arrangement(
            ContractualArrangement(reference_number="C-1", provider_id=p.id)
        )
        await reg.register_arrangement(
            ContractualArrangement(reference_number="C-2", provider_id=p.id)
        )
        refs = {a.reference_number for a in await reg.arrangements_for_provider(p.id)}
        assert refs == {"C-1", "C-2"}


class TestConcentration:
    async def test_flags_hard_to_substitute_critical_provider(self):
        reg = _register()
        p = await _provider(reg)
        f = await _critical_function(reg)
        await reg.register_arrangement(
            ContractualArrangement(
                reference_number="C-1",
                provider_id=p.id,
                function_ids=[f.id],
                assessment=ServiceAssessment(
                    supports_critical_function=True,
                    substitutability=Substitutability.NOT_SUBSTITUTABLE,
                ),
            )
        )
        summary = await reg.concentration_summary()
        assert summary["providers"] == 1
        assert summary["arrangements"] == 1
        assert summary["critical_or_important_arrangements"] == 1
        assert summary["providers_supporting_critical"] == [p.id]
        assert summary["arrangements_per_provider"][p.id] == 1
        assert len(summary["concentration_flags"]) == 1
        assert summary["concentration_flags"][0]["provider_id"] == p.id

    async def test_easily_substitutable_not_flagged(self):
        reg = _register()
        p = await _provider(reg)
        f = await _critical_function(reg)
        await reg.register_arrangement(
            ContractualArrangement(
                reference_number="C-1",
                provider_id=p.id,
                function_ids=[f.id],
                assessment=ServiceAssessment(
                    supports_critical_function=True,
                    substitutability=Substitutability.EASILY_SUBSTITUTABLE,
                ),
            )
        )
        summary = await reg.concentration_summary()
        assert summary["concentration_flags"] == []


class TestExport:
    async def test_export_maps_to_esa_templates(self):
        reg = _register()
        p = await _provider(reg)
        sub = await reg.register_provider(ICTProvider(name="SubCo"))
        f = await _critical_function(reg)
        await reg.register_arrangement(
            ContractualArrangement(
                reference_number="C-1",
                provider_id=p.id,
                function_ids=[f.id],
                subcontractor_ids=[sub.id],
                assessment=ServiceAssessment(
                    supports_critical_function=True, exit_plan_exists=True
                ),
            )
        )
        register = await reg.export_register()
        assert "Implementing Regulation (EU) 2024/2956" in register["_meta"]["standard"]
        assert len(register["B_05.01"]) == 2  # provider + subcontractor
        assert register["B_06.01"][0]["critical_or_important"] is True
        assert register["B_02.02"][0]["contractual_reference"] == "C-1"
        assert register["B_07.01"][0]["exit_plan_exists"] is True
        # Supply chain: one row per (arrangement, subcontractor).
        assert register["B_05.02"][0]["subcontractor_identifier"] == sub.id
        assert register["B_05.02"][0]["rank"] == 1
