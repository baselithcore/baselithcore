import pytest
from core.swarm.pheromones import PheromoneSystem


@pytest.fixture
def pheromones():
    return PheromoneSystem(decay_rate=0.5, decay_interval=1.0)


class TestPheromoneSystem:
    def test_deposit(self, pheromones):
        location = "loc1"
        ptype = PheromoneSystem.SUCCESS

        pheromones.deposit(ptype, location, intensity=2.0, agent_id="a1")

        signals = pheromones.sense(location)
        assert signals[ptype] == 2.0
        assert location in pheromones.get_active_locations()

    def test_deposit_reinforcement(self, pheromones):
        location = "loc1"
        ptype = PheromoneSystem.SUCCESS

        pheromones.deposit(ptype, location, intensity=1.0)
        pheromones.deposit(ptype, location, intensity=1.0)

        signals = pheromones.sense(location)
        assert signals[ptype] == 2.0

    def test_decay_all(self, pheromones):
        location = "loc1"
        ptype = PheromoneSystem.SUCCESS

        # Initial deposit
        pheromones.deposit(ptype, location, intensity=2.0)

        # Apply decay (rate=0.5)
        pheromones.decay_all()

        signals = pheromones.sense(location)
        assert signals[ptype] == 1.5  # 2.0 - 0.5

        # Apply more decay until inactive
        pheromones.decay_all()  # 1.0
        pheromones.decay_all()  # 0.5
        pheromones.decay_all()  # 0.0 -> removed

        signals = pheromones.sense(location)
        assert ptype not in signals
        assert location not in pheromones.get_active_locations()

    def test_sense_type(self, pheromones):
        ptype = PheromoneSystem.SUCCESS
        pheromones.deposit(ptype, "loc1", intensity=1.0)
        pheromones.deposit(ptype, "loc2", intensity=2.0)
        pheromones.deposit(PheromoneSystem.FAILURE, "loc1", intensity=1.0)

        results = pheromones.sense_type(ptype)
        assert len(results) == 2
        assert results["loc1"] == 1.0
        assert results["loc2"] == 2.0

    def test_get_strongest(self, pheromones):
        ptype = PheromoneSystem.SUCCESS
        pheromones.deposit(ptype, "loc1", intensity=1.0)
        pheromones.deposit(ptype, "loc2", intensity=3.0)
        pheromones.deposit(ptype, "loc3", intensity=2.0)

        strongest = pheromones.get_strongest(ptype)
        assert strongest == "loc2"

        # Test exclude
        strongest_ex = pheromones.get_strongest(ptype, exclude={"loc2"})
        assert strongest_ex == "loc3"

    def test_follow_gradient(self, pheromones):
        ptype = PheromoneSystem.SUCCESS
        pheromones.deposit(ptype, "start", intensity=1.0)
        pheromones.deposit(ptype, "n1", intensity=0.5)
        pheromones.deposit(ptype, "n2", intensity=2.0)
        pheromones.deposit(ptype, "n3", intensity=1.5)

        next_hop = pheromones.follow_gradient("start", ptype, ["n1", "n2", "n3"])
        assert next_hop == "n2"

    def test_evaporate(self, pheromones):
        location = "loc1"
        pheromones.deposit(PheromoneSystem.SUCCESS, location, 1.0)
        pheromones.deposit(PheromoneSystem.FAILURE, location, 1.0)

        # Evaporate specific type
        pheromones.evaporate(location, PheromoneSystem.SUCCESS)
        signals = pheromones.sense(location)
        assert PheromoneSystem.SUCCESS not in signals
        assert PheromoneSystem.FAILURE in signals

        # Evaporate all
        pheromones.evaporate(location)
        assert location not in pheromones.get_active_locations()
