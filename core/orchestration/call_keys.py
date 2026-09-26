"""Content-addressed, path-independent identities for tool calls.

Both durability layers — the idempotency ledger
(:mod:`core.orchestration.idempotency`) and the checkpoint step map
(:mod:`core.orchestration.checkpoint`) — need to recognise "the same effect,
requested again" when a run resumes. They used to recognise it by *position*:
the ledger key hashed the call's ordinal in the run, and the checkpoint key
began with the replay cursor. Position is only stable when the resumed pass
takes exactly the path the crashed one took, and an LLM-driven loop is not
deterministic: regenerate the turn and the model may call ``lookup`` before
``charge_card`` this time, or add a call in between. Every call after the first
divergence then derived a fresh key and ran again — the payment included.

The identity used here is the *content* of the call plus how many times that
exact content was already requested in the run:

    key = H(tenant, run_id, tool, canonical_json(args), occurrence)

* **Path-independent.** ``charge_card({"amount": 10})`` maps to the same key
  whether it is the first call of the resumed pass or the fifth.
* **Still distinct for deliberate repeats.** The second identical call in the
  same run has ``occurrence == 1`` and therefore its own key, so a loop that
  really means to send two identical notifications sends two.
* **Tenant-scoped.** Run ids are frequently caller-chosen (``order-42``); two
  tenants choosing the same one must not replay each other's results out of a
  ledger whose primary key is global.

**Why not the provider's ``tool_use`` id?** It looks like the natural key, but
it is minted by the provider per generated turn. A resumed run regenerates the
turn, and the regenerated turn carries new ids for the very same calls, so a
key built on them would never match anything recorded before the crash.

Occurrences are counted per *pass*: a resumed run starts a fresh
:class:`CallOccurrences`, so its first ``charge_card({"amount": 10})`` is
occurrence 0 again and lands on the key the crashed pass recorded. Within one
turn, identical calls are interchangeable by construction, so the order in
which concurrent calls draw their occurrence does not matter.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

__all__ = [
    "KEY_SCHEME_VERSION",
    "CallOccurrences",
    "call_step_key",
    "canonical_arguments",
    "derive_call_key",
]

#: Version tag mixed into every content-addressed key. It domain-separates the
#: digest from the positional scheme it replaced, so a new key can never equal
#: a legacy one by construction, and names the scheme in checkpoint step keys.
KEY_SCHEME_VERSION = "v2"


def canonical_arguments(args: Any) -> str:
    """Canonical JSON text for a call's arguments.

    Args:
        args: The call arguments, normally a dict.

    Returns:
        Sorted-key, whitespace-free JSON. A value JSON cannot encode falls back
        to its ``repr``: stable within a process but not necessarily across
        them, which is the honest outcome for a value the runtime cannot
        canonicalise.
    """
    value = {} if args is None else args
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
    except (TypeError, ValueError):
        if isinstance(value, dict):
            return repr(sorted(value.items(), key=lambda kv: str(kv[0])))
        return repr(value)


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def derive_call_key(
    run_id: str,
    tool: str,
    args: Any,
    occurrence: int,
    *,
    tenant_id: str = "",
) -> str:
    """Derive the ledger key identifying one requested effect.

    Args:
        run_id: The run the call belongs to. Supplying a stable ``run_id``
            across attempts is what makes deduplication possible at all.
        tool: Tool name.
        args: Call arguments; key order and spelling do not matter.
        occurrence: How many identical ``(tool, args)`` calls this pass already
            requested — ``0`` for the first. See :class:`CallOccurrences`.
        tenant_id: Owning tenant, so equal run ids chosen by two tenants never
            share a key.

    Returns:
        A 64-character hex SHA-256 digest; the key carries no payload.
    """
    return _digest(
        KEY_SCHEME_VERSION,
        tenant_id,
        run_id,
        tool,
        canonical_arguments(args),
        str(occurrence),
    )


def call_step_key(tool: str, args: Any, occurrence: int) -> str:
    """Checkpoint step key for one requested effect.

    A checkpoint is already scoped to one run, so run and tenant add nothing.
    The key stays readable in a stored checkpoint (``v2:<tool>:<hash>:<n>``),
    and its prefix can never collide with a legacy ``<cursor>:<tool>:<hash>``
    key, whose first segment is an integer.

    Args:
        tool: Tool (or workflow node) name.
        args: Call arguments.
        occurrence: Occurrence of this ``(tool, args)`` pair within the pass.

    Returns:
        The step key.
    """
    args_hash = _digest(canonical_arguments(args))[:16]
    return f"{KEY_SCHEME_VERSION}:{tool}:{args_hash}:{occurrence}"


class CallOccurrences:
    """Counts identical ``(tool, args)`` requests within one pass of a run.

    One instance per pass: a resumed run starts over at zero, which is exactly
    what lets its calls land on the keys the crashed pass recorded.
    """

    def __init__(self) -> None:
        self._seen: dict[tuple[str, str], int] = {}

    def next(self, tool: str, args: Any) -> int:
        """Claim the next occurrence number for ``(tool, args)``.

        Args:
            tool: Tool name.
            args: Call arguments.

        Returns:
            ``0`` the first time this pair is requested in the pass, then
            ``1``, ``2``, ….
        """
        identity = (tool, canonical_arguments(args))
        occurrence = self._seen.get(identity, 0)
        self._seen[identity] = occurrence + 1
        return occurrence
