import pytest
from unittest.mock import AsyncMock
from core.adversarial.boundary import BoundaryTester
from core.adversarial.types import AttackCategory, AttackStatus


@pytest.mark.asyncio
async def test_boundary_tester_initialization():
    tester = BoundaryTester()
    assert tester.boundaries is not None
    assert "no_harmful_content" in tester.boundaries


@pytest.mark.asyncio
async def test_generate_boundary_attacks():
    tester = BoundaryTester()
    attacks = tester.generate_boundary_attacks()
    assert len(attacks) > 0
    assert attacks[0].category == AttackCategory.BOUNDARY_VIOLATION


@pytest.mark.asyncio
async def test_test_boundary_enforced():
    tester = BoundaryTester()
    attacks = tester.generate_boundary_attacks("no_harmful_content")
    attack = attacks[0]

    # Mock agent refusing the request
    mock_agent = AsyncMock(return_value="I cannot assist with that.")

    result = await tester.test_boundary(attack, mock_agent)

    assert result.status == AttackStatus.BLOCKED
    assert result.success is False
    assert result.detection_triggered is True


@pytest.mark.asyncio
async def test_test_boundary_violation():
    tester = BoundaryTester()
    attacks = tester.generate_boundary_attacks("no_harmful_content")
    attack = attacks[0]

    # Mock agent complying with harmful request (violation)
    mock_agent = AsyncMock(return_value="Here's how to do this: Step 1...")

    result = await tester.test_boundary(attack, mock_agent)

    assert result.status == AttackStatus.SUCCESS
    assert result.success is True
    assert result.detection_triggered is False


@pytest.mark.asyncio
async def test_run_all_tests():
    tester = BoundaryTester()
    mock_agent = AsyncMock(return_value="I cannot do that.")

    results = await tester.run_all_tests(mock_agent)

    assert len(results) > 0
    for res in results:
        assert res.status == AttackStatus.BLOCKED
