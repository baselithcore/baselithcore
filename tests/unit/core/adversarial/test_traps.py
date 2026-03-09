import pytest
from unittest.mock import AsyncMock
from core.adversarial.traps import HallucinationTrap
from core.adversarial.types import AttackStatus


@pytest.mark.asyncio
async def test_trap_initialization():
    trap = HallucinationTrap()
    assert len(trap.nonexistent_refs) > 0


@pytest.mark.asyncio
async def test_test_trap_success():
    trap = HallucinationTrap()
    attack_vector = trap.generate_reference_traps(1)[0]

    # Agent admits it doesn't know (Good behavior)
    mock_agent = AsyncMock(
        return_value="I don't have information about that reference."
    )

    result = await trap.test_trap(attack_vector, mock_agent)

    assert result.status == AttackStatus.BLOCKED
    assert result.success is False  # Not a hallucination


@pytest.mark.asyncio
async def test_test_trap_failure():
    trap = HallucinationTrap()
    attack_vector = trap.generate_reference_traps(1)[0]

    # Agent hallucinated (Bad behavior)
    mock_agent = AsyncMock(
        return_value="Yes, according to the paper, the findings are..."
    )

    result = await trap.test_trap(attack_vector, mock_agent)

    assert result.status == AttackStatus.SUCCESS
    assert result.success is True  # Hallucination confirmed


@pytest.mark.asyncio
async def test_run_all_traps():
    trap = HallucinationTrap()
    mock_agent = AsyncMock(return_value="I'm not aware of this.")

    results = await trap.run_all_traps(mock_agent, count_per_category=1)

    assert len(results) > 0
    assert all(r.status == AttackStatus.BLOCKED for r in results)
