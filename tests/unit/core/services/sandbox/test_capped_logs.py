"""Container output is read against a byte budget, never whole."""

from __future__ import annotations

from unittest.mock import MagicMock

from baselith.core.services.sandbox._logs import TRUNCATION_NOTICE, read_capped_logs


def _container(chunks):
    container = MagicMock()
    consumed: list[bytes] = []

    def logs(stdout=True, stderr=True, stream=False):
        assert stream is True, "the log must be streamed, not buffered"

        def gen():
            for chunk in chunks:
                consumed.append(chunk)
                yield chunk

        return gen()

    container.logs.side_effect = logs
    return container, consumed


def test_short_output_is_returned_whole() -> None:
    container, _ = _container([b"hello ", b"world\n"])
    assert read_capped_logs(container, stdout=True, stderr=False) == "hello world"


def test_read_stops_at_the_budget() -> None:
    flood = [b"x" * 1000] * 10_000  # 10 MB the container would like to hand over
    container, consumed = _container(flood)

    out = read_capped_logs(container, stdout=True, stderr=False, limit=4096)

    assert out.endswith(TRUNCATION_NOTICE)
    assert len(out) == 4096 + len(TRUNCATION_NOTICE)
    assert len(consumed) <= 5  # stopped pulling, did not drain the stream


def test_a_character_cut_by_the_budget_does_not_raise() -> None:
    container, _ = _container(["é".encode() * 10])
    out = read_capped_logs(container, stdout=True, stderr=False, limit=3)
    assert out.startswith("é")


def test_a_client_returning_bytes_is_still_capped() -> None:
    container = MagicMock()
    container.logs.return_value = b"y" * 100
    out = read_capped_logs(container, stdout=True, stderr=False, limit=10)
    assert out == "y" * 10 + TRUNCATION_NOTICE
