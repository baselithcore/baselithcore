"""Changes to the DORA Register of Information are on the audit trail."""

from typing import Any

import pytest

from baselith.core.thirdparty import (
    ContractualArrangement,
    FunctionCriticality,
    ICTFunction,
    ICTProvider,
    RegisterOfInformation,
    RegisterValidationError,
    ServiceAssessment,
)


@pytest.fixture()
def audit(monkeypatch) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    class _Logger:
        async def log(self, event_type, **kwargs):
            records.append({"event_type": event_type.value, **kwargs})

    monkeypatch.setattr(
        "baselith.core.thirdparty.register.get_audit_logger", lambda: _Logger()
    )
    return records


async def test_each_kind_of_change_is_recorded(audit):
    register = RegisterOfInformation()
    provider = await register.register_provider(
        ICTProvider(name="AcmeCloud", country="IE", lei="X" * 20)
    )
    function = await register.register_function(
        ICTFunction(name="Settlement", criticality=FunctionCriticality.CRITICAL)
    )
    await register.register_arrangement(
        ContractualArrangement(
            reference_number="CTR-1",
            provider_id=provider.id,
            function_ids=[function.id],
            assessment=ServiceAssessment(supports_critical_function=True),
        )
    )

    assert [(r["event_type"], r["action"]) for r in audit] == [
        ("compliance.register", "provider"),
        ("compliance.register", "function"),
        ("compliance.register", "arrangement"),
    ]
    assert all(r["details"]["register"] == "dora" for r in audit)
    assert audit[2]["resource"] == "CTR-1"
    assert audit[2]["details"]["supports_critical_function"] is True


async def test_a_refused_change_leaves_no_record(audit):
    register = RegisterOfInformation()

    with pytest.raises(RegisterValidationError):
        await register.register_arrangement(
            ContractualArrangement(reference_number="CTR-2", provider_id="nope")
        )

    assert audit == []
