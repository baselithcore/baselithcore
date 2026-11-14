"""Text encoding of a vector for the pgvector ``vector`` type."""

from __future__ import annotations

from collections.abc import Sequence

import orjson

#: Every byte a JSON array of plain numbers can contain.
_NUMERIC_ARRAY_BYTES = b"0123456789eE.+-,[]"


def _encode_vector(vector: Sequence[float]) -> str:
    """pgvector text encoding: '[0.1,0.2,...]'.

    A JSON array of floats *is* that encoding, and orjson writes the same
    shortest round-trip digits ``repr`` does — in C, twelve times faster than
    the per-float join this replaced (which held the event loop for ~0.25 ms
    per 1024-dimension vector, on every point of every upsert). Anything
    that does not come out as bare numbers (a numpy array, a non-finite value
    written as ``null``, a bool, a numeric string) takes the explicit float
    path instead.
    """
    if type(vector) is list:
        try:
            encoded: bytes = orjson.dumps(vector)
        except TypeError:
            encoded = b"?"
        if not encoded.translate(None, _NUMERIC_ARRAY_BYTES):
            text: str = encoded.decode()
            return text
    return "[" + ",".join(repr(float(v)) for v in vector) + "]"


__all__ = ["_encode_vector"]
