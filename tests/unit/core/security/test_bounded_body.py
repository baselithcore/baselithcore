"""Bounded body reading: a compressed body is capped on its decoded size
while it is inflated, not after httpx has expanded a whole chunk."""

from __future__ import annotations

import gzip
import zlib

import httpx
import pytest

from baselith.core.security.bounded_body import (
    BOUNDED_ACCEPT_ENCODING,
    BodyTooLargeError,
    UnsupportedEncodingError,
    decoded_headers,
    read_bounded,
    read_truncated,
)


class _WireStream(httpx.AsyncByteStream):
    """The body as a server sends it: raw bytes in 16 KiB network chunks."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    async def __aiter__(self):
        for start in range(0, len(self._body), 16384):
            yield self._body[start : start + 16384]


async def _send(body: bytes, headers: dict[str, str]) -> httpx.Response:
    # ``content=`` would make httpx decode the body eagerly; a stream keeps
    # it on the wire until the reader pulls it, as a real server response.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers=headers, stream=_WireStream(body))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    request = client.build_request("GET", "https://example.test/")
    return await client.send(request, stream=True)


async def test_identity_body_reads_whole() -> None:
    response = await _send(b"hello", {})
    assert await read_bounded(response, 10) == b"hello"


async def test_identity_body_over_the_cap_is_refused() -> None:
    response = await _send(b"x" * 11, {})
    with pytest.raises(BodyTooLargeError):
        await read_bounded(response, 10)


async def test_declared_identity_length_over_the_cap_is_refused() -> None:
    response = await _send(b"x" * 11, {"content-length": "11"})
    with pytest.raises(BodyTooLargeError):
        await read_bounded(response, 10)


@pytest.mark.parametrize(
    ("encoding", "compress"),
    [("gzip", gzip.compress), ("deflate", zlib.compress)],
)
async def test_compressed_body_is_inflated(encoding, compress) -> None:
    payload = b"abc" * 1000
    response = await _send(compress(payload), {"content-encoding": encoding})
    assert await read_bounded(response, len(payload)) == payload


async def test_a_gzip_bomb_stops_at_the_cap() -> None:
    """64 MiB of zeros compress to ~64 KiB; the reader must stop near 1 KiB of
    output instead of inflating the whole thing first."""
    bomb = gzip.compress(b"\0" * (64 * 1024 * 1024))
    response = await _send(bomb, {"content-encoding": "gzip"})
    with pytest.raises(BodyTooLargeError):
        await read_bounded(response, 1024)


async def test_small_declared_length_does_not_excuse_a_large_inflation() -> None:
    bomb = gzip.compress(b"\0" * 100_000)
    response = await _send(
        bomb, {"content-encoding": "gzip", "content-length": str(len(bomb))}
    )
    with pytest.raises(BodyTooLargeError):
        await read_bounded(response, 50_000)


@pytest.mark.parametrize("encoding", ["br", "zstd", "gzip, br"])
async def test_unbounded_encodings_are_refused(encoding) -> None:
    response = await _send(b"irrelevant", {"content-encoding": encoding})
    with pytest.raises(UnsupportedEncodingError):
        await read_bounded(response, 1024)


async def test_already_read_response_is_checked_on_its_length() -> None:
    response = await _send(b"x" * 20, {})
    await response.aread()
    with pytest.raises(BodyTooLargeError):
        await read_bounded(response, 10)


async def test_decoded_headers_drop_the_wire_encoding() -> None:
    response = await _send(
        gzip.compress(b"x"), {"content-encoding": "gzip", "x-keep": "1"}
    )
    names = {key.lower() for key, _ in decoded_headers(response)}
    assert "content-encoding" not in names
    assert "content-length" not in names
    assert "x-keep" in names


def test_accept_encoding_lists_only_bounded_decoders() -> None:
    assert {part.strip() for part in BOUNDED_ACCEPT_ENCODING.split(",")} == {
        "gzip",
        "deflate",
    }


async def test_truncated_read_keeps_the_prefix_of_an_oversized_body() -> None:
    response = await _send(b"a" * 50, {"content-length": "50"})
    assert await read_truncated(response, 10) == b"a" * 10


async def test_truncated_read_inflates_a_bomb_only_up_to_the_cap() -> None:
    bomb = gzip.compress(b"\0" * (64 * 1024 * 1024))
    response = await _send(bomb, {"content-encoding": "gzip"})
    assert await read_truncated(response, 1024) == b"\0" * 1024


async def test_truncated_read_still_refuses_unbounded_encodings() -> None:
    response = await _send(b"irrelevant", {"content-encoding": "br"})
    with pytest.raises(UnsupportedEncodingError):
        await read_truncated(response, 1024)
