"""Equality predicates on payload keys, written so the GIN index can serve them."""

from __future__ import annotations

from typing import Any

import orjson

#: Payload keys the service always writes as JSON strings. Only these may use
#: containment: ``payload @> '{"k": "v"}'`` matches a *string* ``v``, while the
#: ``payload->>'k' = 'v'`` text form also matches a number or boolean stored
#: under the key. Any other key keeps the text form, whose meaning it had.
_STRING_KEYS = frozenset({"tenant_id", "document_id"})


def match_fragment(key: str, values: Any, params: list[Any], *, negate: bool) -> str:
    """SQL for "payload ``key`` equals ``values``" (a scalar, or any of a list).

    The index is ``gin (payload jsonb_path_ops)``, which serves ``@>`` and
    nothing else: the ``->>`` form read every row (a parallel sequential scan,
    28-170 ms on 200 000 rows, against 0.16 ms for the containment form).
    Negated conditions keep the text form — ``NOT (payload @> ...)`` is true for
    a row with no such key, where ``NOT (payload->>k = v)`` is NULL and drops it.

    Args:
        key: Payload key.
        values: One value, or a list/tuple/set meaning "match any of these".
        params: Parameter list the fragment's placeholders are appended to.
        negate: Whether the condition sits under a ``must_not``.

    Returns:
        The predicate, without the surrounding ``NOT``.
    """
    many = isinstance(values, (list, tuple, set, frozenset))
    if key in _STRING_KEYS and not negate:
        docs = [
            orjson.dumps({key: str(v)}).decode() for v in (values if many else [values])
        ]
        if many:
            params.append(docs)
            return "payload @> ANY(%s::jsonb[])"
        params.append(docs[0])
        return "payload @> %s::jsonb"
    if many:
        params.extend([str(key), [str(v) for v in values]])
        return "payload->>%s = ANY(%s)"
    params.extend([str(key), str(values)])
    return "payload->>%s = %s"


__all__ = ["match_fragment"]
