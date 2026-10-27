"""Inbound trigger authentication.

A trigger is a webhook *into* the framework: an external system tells a
workflow to run. It reuses the outbound webhooks' signature scheme
(``X-Baselith-Signature: t=<unix_ts>,v1=<hex_hmac_sha256>``, see
:mod:`baselith.core.webhooks.signing`) so a sender needs one signer for both
directions, and adds what an inbound endpoint needs on top: a bounded body,
and refusal of a signature that was already used inside its freshness window
(a captured request cannot be replayed).

The configured secret is a *master* secret. Each workflow signs with a key
derived from it and the workflow id (:func:`derive_workflow_secret`), so a
request captured for one workflow is not valid for another, and handing a
partner the key of one workflow grants nothing else. The key is shown to
operators with ``baselith workflow trigger-secret <id>``.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections import OrderedDict

from pydantic import SecretStr

from baselith.core.webhooks.signing import SIGNATURE_HEADER, verify_signature

__all__ = [
    "MAX_TRIGGER_BODY_BYTES",
    "SIGNATURE_HEADER",
    "TriggerAuthError",
    "TriggerVerifier",
    "derive_workflow_secret",
]

#: Largest body a trigger accepts (1 MiB).
MAX_TRIGGER_BODY_BYTES = 1024 * 1024
_MAX_REMEMBERED = 10_000


def derive_workflow_secret(master: str, workflow_id: str) -> str:
    """The signing key of one workflow, derived from the master secret.

    Args:
        master: The configured ``WORKFLOWS_TRIGGER_SECRET``.
        workflow_id: The workflow the key is for.

    Returns:
        A hex string to use as the HMAC secret when signing that workflow's
        trigger requests.
    """
    return hmac.new(
        master.encode("utf-8"),
        f"baselith-workflow-trigger:{workflow_id}".encode(),
        hashlib.sha256,
    ).hexdigest()


class TriggerAuthError(PermissionError):
    """A trigger request failed authentication."""

    def __init__(self, reason: str, *, replay: bool = False) -> None:
        super().__init__(reason)
        self.replay = replay


class TriggerVerifier:
    """Verifies trigger signatures and rejects replays."""

    def __init__(self, secret: SecretStr | None, tolerance_seconds: int) -> None:
        """
        Args:
            secret: The shared secret; ``None`` makes every request fail.
            tolerance_seconds: Freshness window of the signed timestamp.
        """
        self._secret = secret
        self._tolerance = tolerance_seconds
        self._seen: OrderedDict[str, float] = OrderedDict()

    @property
    def configured(self) -> bool:
        """Whether a secret is set; without one no trigger can be served."""
        return self._secret is not None and bool(self._secret.get_secret_value())

    def verify(self, workflow_id: str, body: bytes, header: str | None) -> None:
        """Authenticate one request.

        The signature covers the body and is made with the workflow's derived
        key, so it is valid for this workflow only.

        Raises:
            TriggerAuthError: No secret configured, header missing or
                malformed, bad or stale signature, or a replay.
        """
        if not self.configured or self._secret is None:
            raise TriggerAuthError("trigger secret is not configured")
        if not header:
            raise TriggerAuthError("missing signature")
        if not verify_signature(
            derive_workflow_secret(self._secret.get_secret_value(), workflow_id),
            body,
            header,
            tolerance_seconds=self._tolerance,
        ):
            raise TriggerAuthError("invalid or expired signature")
        key = f"{workflow_id}:{header}"
        now = time.time()
        self._evict(now)
        if key in self._seen:
            raise TriggerAuthError("signature already used", replay=True)
        self._seen[key] = now

    def _evict(self, now: float) -> None:
        horizon = now - self._tolerance - 1
        while self._seen:
            oldest_key, stamp = next(iter(self._seen.items()))
            if stamp >= horizon and len(self._seen) < _MAX_REMEMBERED:
                break
            self._seen.pop(oldest_key)
