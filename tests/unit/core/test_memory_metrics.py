"""
Unit Tests for Memory Metrics.
"""

import time
from datetime import datetime, timedelta, timezone
from core.memory.metrics import MemoryMetricsCollector, OperationRecord


class TestMemoryMetrics:
    """Tests for MemoryMetricsCollector."""

    def test_record_operation(self):
        """Test recording an operation."""
        collector = MemoryMetricsCollector()
        record = OperationRecord(
            operation="test",
            timestamp=datetime.now(timezone.utc),
            latency_ms=10.0,
            success=True,
        )
        collector.record(record)

        assert len(collector._history) == 1
        assert collector._history[0] == record

    def test_get_metrics(self):
        """Test calculating aggregated metrics."""
        collector = MemoryMetricsCollector()

        collector.record(
            OperationRecord(
                "recall", datetime.now(timezone.utc), 10.0, True, tokens_estimated=100
            )
        )
        collector.record(
            OperationRecord(
                "recall",
                datetime.now(timezone.utc),
                20.0,
                True,
                tokens_estimated=50,
                cache_hit=True,
            )
        )

        metrics = collector.get_metrics()

        assert metrics.retrieval_latency_ms == 15.0  # Average of 10 and 20
        assert metrics.tokens_consumed == 150
        assert metrics.cache_hit_rate == 0.5  # 1 hit out of 2 recalls
        assert metrics.total_retrievals == 2
        assert metrics.total_cache_hits == 1

    def test_window_filtering(self):
        """Test filtering metrics by time window."""
        collector = MemoryMetricsCollector(window_seconds=60)

        old_time = datetime.now(timezone.utc) - timedelta(minutes=5)
        collector.record(OperationRecord("recall", old_time, 10.0, True))

        new_time = datetime.now(timezone.utc)
        collector.record(OperationRecord("recall", new_time, 20.0, True))

        metrics = collector.get_metrics()

        # Should only count the new record
        assert metrics.retrieval_latency_ms == 20.0
        assert metrics.total_retrievals == 2  # Total counts are cumulative
        # But latency average is over window

    def test_tracker_context_manager(self):
        """Test OperationTracker context manager."""
        collector = MemoryMetricsCollector()

        with collector.track_operation("test_op") as tracker:
            tracker.set_tokens(50)
            time.sleep(0.01)  # Ensure non-zero latency

        assert len(collector._history) == 1
        record = collector._history[0]
        assert record.operation == "test_op"
        assert record.tokens_estimated == 50
        assert record.latency_ms > 0
        assert record.success is True

    def test_tracker_exception(self):
        """Test tracker records failure on exception."""
        collector = MemoryMetricsCollector()

        try:
            with collector.track_operation("fail_op"):
                raise ValueError("Fail")
        except ValueError:
            pass

        assert len(collector._history) == 1
        assert collector._history[0].success is False
        assert collector._history[0].operation == "fail_op"

    def test_latency_percentiles(self):
        """Test latency percentile calculation."""
        collector = MemoryMetricsCollector()

        # Helper to add latency
        def add(ms):
            collector.record(
                OperationRecord("op", datetime.now(timezone.utc), ms, True)
            )

        for i in range(1, 101):  # 1 to 100
            add(float(i))

        p = collector.get_latency_percentiles()

        # Exact values depend on implementation details of sorted index logic
        assert 49 <= p["p50"] <= 51
        assert 89 <= p["p90"] <= 91
        assert 98 <= p["p99"] <= 100
