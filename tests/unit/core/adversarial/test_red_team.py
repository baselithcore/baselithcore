import pytest
from unittest.mock import AsyncMock, Mock, patch
from core.adversarial.red_team import RedTeamAgent
from core.adversarial.types import (
    AttackCategory,
    AttackStatus,
    Severity,
    AttackResult,
    AttackVector,
)


@pytest.mark.asyncio
async def test_red_team_initialization():
    agent = RedTeamAgent()
    assert agent.fuzzer is not None
    assert agent.trap is not None
    assert agent.boundary_tester is not None


@pytest.mark.asyncio
async def test_attack_categories_full():
    agent = RedTeamAgent(attack_count_per_category=1)
    mock_target = AsyncMock(return_value="Blocked.")

    # Test all categories including default
    report = await agent.attack(mock_target)  # Default categories
    assert report.total_tests >= 4

    # Test JAILBREAK and DATA_EXTRACTION specifically
    report2 = await agent.attack(
        mock_target,
        categories=[AttackCategory.JAILBREAK, AttackCategory.DATA_EXTRACTION],
    )
    assert len(report2.attack_results) >= 2


@pytest.mark.asyncio
async def test_attack_execution_error():
    agent = RedTeamAgent(attack_count_per_category=1)

    # Mock target that raises exception
    mock_target = AsyncMock(side_effect=ValueError("Test Error"))

    report = await agent.attack(
        mock_target, categories=[AttackCategory.PROMPT_INJECTION]
    )

    assert any(res.status == AttackStatus.FAILED for res in report.attack_results)
    assert any("Test Error" in res.response for res in report.attack_results)


@pytest.mark.asyncio
async def test_llm_detection_verdict():
    mock_llm = AsyncMock()
    mock_llm.generate_response.return_value = " SUCCESS "  # Test trimming

    from core.adversarial.red_team import RedTeamAgent as RTA

    # Patch the actual location where get_llm_service is imported or used
    with patch("core.services.llm.get_llm_service", return_value=mock_llm):
        agent = RTA(llm_detection=True)
        # Force llm_service load
        _ = agent.llm_service

        vector = Mock(
            category=AttackCategory.PROMPT_INJECTION, payload="pay", description="desc"
        )
        result = await agent._analyze_attack_success(vector, "resp")
        assert result is True


@pytest.mark.asyncio
async def test_keyword_heuristics_extended():
    agent = RedTeamAgent()

    # PROMPT_INJECTION compliance
    assert (
        agent._analyze_with_keywords(
            Mock(category=AttackCategory.PROMPT_INJECTION, payload="Ignore"),
            "Sure, I have updated the context.",
        )
        is True
    )

    # DATA_EXTRACTION sensitive
    assert (
        agent._analyze_with_keywords(
            Mock(category=AttackCategory.DATA_EXTRACTION), "The password is 123"
        )
        is True
    )


def test_report_critical_penalty():
    agent = RedTeamAgent()
    # Mock results that include successful attacks (vulnerabilities)
    results = [
        AttackResult(
            attack_vector=AttackVector(
                category=AttackCategory.PROMPT_INJECTION,
                severity_if_successful=Severity.CRITICAL,
            ),
            status=AttackStatus.SUCCESS,
            success=True,
        ),
        AttackResult(
            attack_vector=AttackVector(
                category=AttackCategory.JAILBREAK,
                severity_if_successful=Severity.CRITICAL,
            ),
            status=AttackStatus.SUCCESS,
            success=True,
        ),
    ]
    report = agent._generate_report("target", results, 0.1)
    # 100 - 30 - 30 = 40
    assert report.score == 40
    assert any("CRITICAL" in rec for rec in report.recommendations)


@pytest.mark.asyncio
async def test_quick_scan_exception():
    agent = RedTeamAgent()
    mock_target = AsyncMock(side_effect=RuntimeError("Fail"))

    summary = await agent.quick_scan(mock_target)
    assert summary["tests_run"] > 0
    # No attacks blocked because they all errored
    assert summary["attacks_blocked"] == 0
