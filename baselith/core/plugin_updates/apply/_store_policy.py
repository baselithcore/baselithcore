"""The run store's update policy and its pending proposal (split for the size cap).

Files under the store root: ``policy.json`` (the live :class:`UpdatePolicy`),
``policy.proposal.json`` (a change waiting for a second administrator) and
``policy.log.jsonl`` (one line per change: operation, actor, and the SHA-256 of
the document before and after, never the document itself). Every mutation runs
under ``locks/.policy.lock`` (a dot name: no plugin lock can share it) and writes by atomic replace, so a reader always
sees a whole document. A missing or invalid ``policy.json`` reads as
:data:`POLICY_OFF`; an expired proposal is inert and cannot be approved.
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from ..policy import POLICY_OFF, PolicyProposal, UpdatePolicy
from ._store_io import _atomic_write, _now, _read

logger = logging.getLogger(__name__)

_POLICY = "policy.json"
_PROPOSAL = "policy.proposal.json"
_JOURNAL = "policy.log.jsonl"


class PolicyRefused(ValueError):
    """A proposal could not be approved; ``code`` says why.

    ``self_approval`` (the approver proposed it), ``no_proposal``, ``expired``,
    ``changed`` (the proposal is not the one the approver saw, or no digest was
    given) or ``pending`` (another actor's proposal is still waiting: it must be
    discarded before a new one replaces it).
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _sha(text: str | None) -> str | None:
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None


class PolicyMixin:
    """Policy document, proposal and journal of :class:`~.store.RunStore`."""

    _root: Path

    @contextmanager
    def _policy_lock(self) -> Iterator[None]:
        path = self._root / "locks" / ".policy.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _policy_journal(self, entry: dict[str, object]) -> None:
        path = self._root / _JOURNAL
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def get_policy(self) -> UpdatePolicy:
        """The live policy; :data:`POLICY_OFF` when missing or invalid."""
        text = _read(self._root / _POLICY)
        if text is None:
            return POLICY_OFF
        try:
            return UpdatePolicy.model_validate_json(text)
        except ValueError:
            logger.debug("update_policy_unreadable: treated as off")
            return POLICY_OFF

    def _write_policy(
        self,
        policy: UpdatePolicy,
        *,
        op: str,
        actor: str,
        at: datetime,
        extra: dict[str, object] | None = None,
    ) -> None:
        path = self._root / _POLICY
        previous = _read(path)
        text = policy.model_dump_json()
        _atomic_write(path, text)
        self._policy_journal(
            {
                "at": at.isoformat(),
                "op": op,
                "actor": actor,
                "previous_sha256": _sha(previous),
                "sha256": _sha(text),
                **(extra or {}),
            }
        )

    def set_policy(
        self, policy: UpdatePolicy, *, actor: str, now: datetime | None = None
    ) -> None:
        """Replace the live policy directly (the trusted host shell)."""
        with self._policy_lock():
            self._write_policy(policy, op="set", actor=actor, at=_now(now))

    def _read_proposal(self) -> PolicyProposal | None:
        text = _read(self._root / _PROPOSAL)
        if text is None:
            return None
        try:
            return PolicyProposal.model_validate_json(text)
        except ValueError:
            logger.debug("update_policy_proposal_unreadable: ignored")
            return None

    def propose_policy(
        self,
        policy: UpdatePolicy,
        *,
        actor: str,
        ttl_seconds: int,
        now: datetime | None = None,
    ) -> PolicyProposal:
        """Store ``policy`` as the pending proposal.

        It replaces an expired proposal or the actor's own; a live proposal of
        another actor is refused ``pending`` until someone discards it, so a
        proposal under review is never swapped behind its reviewer's back.
        """
        at = _now(now)
        proposal = PolicyProposal(
            policy=policy,
            proposed_by=actor,
            proposed_at=at,
            expires_at=at + timedelta(seconds=ttl_seconds),
        )
        with self._policy_lock():
            current = self._read_proposal()
            if (
                current is not None
                and current.expires_at > at
                and current.proposed_by != actor
            ):
                raise PolicyRefused("pending")
            _atomic_write(self._root / _PROPOSAL, proposal.model_dump_json())
            self._policy_journal(
                {
                    "at": at.isoformat(),
                    "op": "propose",
                    "actor": actor,
                    "proposal_sha256": proposal.digest,
                }
            )
        return proposal

    def pending_proposal(self, now: datetime | None = None) -> PolicyProposal | None:
        """The proposal still waiting for approval; ``None`` once expired."""
        proposal = self._read_proposal()
        if proposal is None or proposal.expires_at <= _now(now):
            return None
        return proposal

    def approve_policy(
        self,
        *,
        approver: str,
        expected_digest: str,
        now: datetime | None = None,
    ) -> UpdatePolicy:
        """Make the pending proposal live; :class:`PolicyRefused` when it cannot.

        ``expected_digest`` is the :attr:`PolicyProposal.digest` the approver
        was shown, and it is mandatory: an empty digest, or a proposal replaced
        in the meantime, is refused ``changed`` (an approver never approves a
        document they did not see).
        """
        at = _now(now)
        with self._policy_lock():
            proposal = self._read_proposal()
            if proposal is None:
                raise PolicyRefused("no_proposal")
            if not expected_digest or not hmac.compare_digest(
                proposal.digest.encode("ascii"),
                expected_digest.encode("utf-8", "replace"),
            ):
                raise PolicyRefused("changed")
            if proposal.expires_at <= at:
                raise PolicyRefused("expired")
            if proposal.proposed_by == approver:
                raise PolicyRefused("self_approval")
            self._write_policy(
                proposal.policy,
                op="approve",
                actor=approver,
                at=at,
                extra={
                    "proposed_by": proposal.proposed_by,
                    "proposal_sha256": proposal.digest,
                },
            )
            (self._root / _PROPOSAL).unlink(missing_ok=True)
        return proposal.policy

    def discard_proposal(
        self, *, actor: str = "unknown", now: datetime | None = None
    ) -> None:
        """Drop the pending proposal, if any (expired ones included)."""
        with self._policy_lock():
            path = self._root / _PROPOSAL
            if not path.exists():
                return
            path.unlink(missing_ok=True)
            self._policy_journal(
                {"at": _now(now).isoformat(), "op": "discard", "actor": actor}
            )


__all__ = ["PolicyMixin", "PolicyRefused"]
