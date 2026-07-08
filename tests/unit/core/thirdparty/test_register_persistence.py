"""Durability + round-trip tests for the DORA register SQLite backend."""

from datetime import date

import core.config.thirdparty as cfg
import core.thirdparty.register as reg_mod
from core.thirdparty import (
    ContractualArrangement,
    DataSensitivity,
    FunctionCriticality,
    ICTFunction,
    ICTProvider,
    InMemoryRegisterStore,
    ProviderType,
    ServiceAssessment,
    Substitutability,
)
from core.thirdparty.persistence import SQLiteRegisterStore


def _provider() -> ICTProvider:
    return ICTProvider(
        name="AcmeCloud",
        lei="X" * 20,
        provider_type=ProviderType.LEGAL_ENTITY,
        country="IE",
        is_critical_designated=True,
        total_annual_expense=1234.5,
        currency="EUR",
    )


def _function() -> ICTFunction:
    return ICTFunction(
        name="Settlement",
        criticality=FunctionCriticality.CRITICAL,
        licensed_activity="payments",
        reasons_for_criticality="core",
    )


def _assessment() -> ServiceAssessment:
    return ServiceAssessment(
        supports_critical_function=True,
        substitutability=Substitutability.NOT_SUBSTITUTABLE,
        exit_plan_exists=True,
        processes_personal_data=True,
        data_sensitivity=DataSensitivity.HIGH,
    )


def _arrangement(provider_id: str, function_id: str) -> ContractualArrangement:
    return ContractualArrangement(
        reference_number="C-1",
        provider_id=provider_id,
        function_ids=[function_id],
        ict_service_type="hosting",
        start_date=date(2026, 1, 1),
        end_date=date(2027, 1, 1),
        notice_period_days=90,
        governing_law_country="IE",
        annual_cost=5000.0,
        data_locations=["IE", "DE"],
        subcontractor_ids=["sub-1"],
        assessment=_assessment(),
    )


class TestRoundTrip:
    def test_provider_round_trips(self):
        p = _provider()
        assert ICTProvider.from_dict(p.to_dict()) == p

    def test_function_round_trips(self):
        f = _function()
        assert ICTFunction.from_dict(f.to_dict()) == f

    def test_assessment_round_trips(self):
        a = _assessment()
        assert ServiceAssessment.from_dict(a.to_dict()) == a

    def test_arrangement_round_trips(self):
        arr = _arrangement("prov-1", "fn-1")
        assert ContractualArrangement.from_dict(arr.to_dict()) == arr

    def test_minimal_arrangement_round_trips(self):
        arr = ContractualArrangement(reference_number="C-2", provider_id="p")
        assert ContractualArrangement.from_dict(arr.to_dict()) == arr


class TestDurability:
    async def test_fresh_store_reads_back_all_three_collections(self, tmp_path):
        db = tmp_path / "register.db"
        store = SQLiteRegisterStore(str(db))
        provider = _provider()
        function = _function()
        arrangement = _arrangement(provider.id, function.id)
        await store.save_provider(provider)
        await store.save_function(function)
        await store.save_arrangement(arrangement)
        store.close()

        store2 = SQLiteRegisterStore(str(db))
        assert await store2.get_provider(provider.id) == provider
        assert await store2.get_function(function.id) == function
        assert await store2.get_arrangement("C-1") == arrangement
        assert await store2.list_providers() == [provider]
        assert await store2.list_functions() == [function]
        assert await store2.list_arrangements() == [arrangement]
        store2.close()

    async def test_missing_returns_none(self, tmp_path):
        store = SQLiteRegisterStore(str(tmp_path / "register.db"))
        assert await store.get_provider("nope") is None
        assert await store.get_function("nope") is None
        assert await store.get_arrangement("nope") is None
        store.close()


class TestWiring:
    def test_default_unset_builds_in_memory_store(self, monkeypatch):
        monkeypatch.delenv("THIRDPARTY_REGISTER_DB_PATH", raising=False)
        monkeypatch.setattr(cfg, "_register_config", None)
        monkeypatch.setattr(reg_mod, "_register", None)
        register = reg_mod.get_register()
        assert isinstance(register.store, InMemoryRegisterStore)

    def test_env_path_selects_sqlite_store(self, monkeypatch, tmp_path):
        monkeypatch.setenv("THIRDPARTY_REGISTER_DB_PATH", str(tmp_path / "register.db"))
        monkeypatch.setattr(cfg, "_register_config", None)
        monkeypatch.setattr(reg_mod, "_register", None)
        register = reg_mod.get_register()
        assert isinstance(register.store, SQLiteRegisterStore)
