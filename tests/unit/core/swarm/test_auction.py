import pytest

from core.swarm.auction import TaskAuction
from core.swarm.types import Task, Bid, AgentProfile, TaskPriority
from core.config.swarm import AuctionConfig


@pytest.fixture
def auction_config():
    return AuctionConfig(min_bids=2, max_bids=5)


@pytest.fixture
def auction(auction_config):
    return TaskAuction(config=auction_config)


@pytest.fixture
def task():
    return Task(
        description="Test Task",
        required_capabilities=["coding"],
        priority=TaskPriority.HIGH,
    )


@pytest.fixture
def agents():
    return [
        AgentProfile(id="a1", name="Agent 1", capabilities=[]),
        AgentProfile(id="a2", name="Agent 2", capabilities=[]),
        AgentProfile(id="a3", name="Agent 3", capabilities=[]),
    ]


class TestTaskAuction:
    def test_announce_task(self, auction, task):
        auction.announce_task(task)
        assert task.id in auction.get_pending_auctions()
        assert auction.get_bids(task.id) == []

    def test_submit_bid(self, auction, task, agents):
        auction.announce_task(task)

        bid = Bid(agent_id=agents[0].id, task_id=task.id, score=0.8)
        assert auction.submit_bid(bid) is True

        # Verify bid recorded
        bids = auction.get_bids(task.id)
        assert len(bids) == 1
        assert bids[0] == bid

    def test_submit_bid_no_auction(self, auction, task, agents):
        # Determine submitting bid for unknown task fails
        bid = Bid(agent_id=agents[0].id, task_id="unknown", score=0.8)
        assert auction.submit_bid(bid) is False

    def test_submit_duplicate_bid(self, auction, task, agents):
        auction.announce_task(task)

        bid1 = Bid(agent_id=agents[0].id, task_id=task.id, score=0.8)
        auction.submit_bid(bid1)

        # Duplicate bid from same agent should fail
        bid2 = Bid(agent_id=agents[0].id, task_id=task.id, score=0.9)
        assert auction.submit_bid(bid2) is False
        assert len(auction.get_bids(task.id)) == 1

    def test_resolve_auction(self, auction, task, agents):
        auction.announce_task(task)

        # Submit bids
        auction.submit_bid(Bid(agent_id=agents[0].id, task_id=task.id, score=0.6))
        auction.submit_bid(
            Bid(agent_id=agents[1].id, task_id=task.id, score=0.9)
        )  # Winner

        winner_id = auction.resolve(task.id)
        assert winner_id == agents[1].id
        assert task.assigned_to == agents[1].id
        assert task.status == "assigned"
        assert task.id not in auction.get_pending_auctions()

    def test_resolve_insufficient_bids(self, auction, task, agents):
        auction.announce_task(task)

        # Submit only 1 bid (min is 2)
        auction.submit_bid(Bid(agent_id=agents[0].id, task_id=task.id, score=0.9))

        winner_id = auction.resolve(task.id)
        assert winner_id is None
        assert task.status == "pending"

    def test_calculate_bid(self, auction, task, agents):
        agent = agents[0]
        bid = auction.calculate_bid(agent, task)

        assert bid.agent_id == agent.id
        assert bid.task_id == task.id
        assert isinstance(bid.score, float)
        assert isinstance(bid.estimated_time, float)

    def test_cancel_auction(self, auction, task):
        auction.announce_task(task)
        assert auction.cancel_auction(task.id) is True
        assert task.id not in auction.get_pending_auctions()
        assert task.status == "cancelled"
