"""Cross-process once-only guard for update announcements.

Every uvicorn worker, and every replica, runs its own check loop and would emit
the same ``plugin.update_available`` / ``system.update_available`` event. The
gate lets exactly one process announce a given key (``<plugin>@<version>`` or
``system:<version>``) using Redis ``SET NX`` when the deployment runs on Redis
(shared by every replica), else an ``O_EXCL`` lock file in the cache directory
(shared by the workers of one host).

The claim is two-phase so a crash cannot swallow a notice: :meth:`claim` takes a
short lease, :meth:`commit` records a long-lived ``done`` marker once the event
went out, and :meth:`release` drops the lease when the emit failed. A lease
older than :data:`LEASE_SECONDS` without a ``done`` marker is stale and can be
claimed again.

Failure policy: a Redis error falls back to the lock file; a lock file that
cannot be written announces anyway. A duplicate notice is a nuisance, a lost
security notice is not.

Keys are namespaced per deployment so two deployments that share one Redis (or
one cache directory) do not suppress each other's notices.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

LOCK_DIRNAME = "announced"
REDIS_KEY_PREFIX = "baselith:updates:announced:"
#: Outlives any release cadence; after expiry a still-current version could be
#: announced once more, which is harmless.
ANNOUNCE_TTL_SECONDS = 400 * 24 * 3600
#: How long an unfinished claim blocks other processes before it is reclaimable.
LEASE_SECONDS = 600

#: A takeover lock older than this belongs to a crashed process.
TAKEOVER_STALE_SECONDS = 60

RedisFactory = Callable[[], Any]


def _digest(namespace: str, key: str) -> str:
    return hashlib.sha256(f"{namespace}\n{key}".encode()).hexdigest()


class AnnouncementGate:
    """Claims announcement keys so that one process emits each."""

    def __init__(
        self,
        root: Path,
        *,
        redis_factory: RedisFactory | None = None,
        ttl_seconds: int = ANNOUNCE_TTL_SECONDS,
        lease_seconds: int = LEASE_SECONDS,
        namespace: str = "",
    ) -> None:
        """Create a gate.

        Args:
            root: The update cache directory; lock files go in ``announced/``.
            redis_factory: Returns an asyncio Redis client; called per
                operation, inside the running loop (the shared pool is per loop).
            ttl_seconds: Expiry of the Redis ``done`` marker.
            lease_seconds: How long an unfinished claim blocks other processes.
            namespace: Deployment identity mixed into every key and lock name.
        """
        self._root = root
        self._redis_factory = redis_factory
        self._ttl = max(1, ttl_seconds)
        self._lease = max(1, lease_seconds)
        self._namespace = namespace
        self._via: dict[str, str] = {}

    # -- lock files ---------------------------------------------------------

    def _paths(self, key: str) -> tuple[Path, Path]:
        base = self._root / LOCK_DIRNAME / _digest(self._namespace, key)
        return base.with_suffix(".lock"), base.with_suffix(".done")

    def _create_lock(self, lock: Path, key: str) -> bool:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"{time.time()}\n{os.getpid()}\n{key}\n")
        return True

    def claim_file(self, key: str) -> bool:
        """Lease ``key`` with an ``O_EXCL`` lock file; True for the first caller.

        A finished key (``done`` marker) is never claimed again; a lock older
        than the lease is stale and is taken over by exactly one caller.

        Raises:
            OSError: The lock directory or file cannot be created.
        """
        lock, done = self._paths(key)
        lock.parent.mkdir(parents=True, exist_ok=True)
        if done.exists():
            return False
        if self._create_lock(lock, key):
            return True
        return self._take_over(lock, key)

    def _take_over(self, lock: Path, key: str) -> bool:
        """Replace ``lock`` when it is stale; one contender at a time.

        The replacement is serialised by a second ``O_EXCL`` lock, and the
        claim lock is re-checked once that is held: a contender that lost the
        race finds the winner's fresh lock and backs off.
        """
        try:
            age = time.time() - lock.stat().st_mtime
        except FileNotFoundError:
            return self._create_lock(lock, key)
        if age < self._lease:
            return False
        guard = lock.with_name(f"{lock.name}.takeover")
        if not self._create_lock(guard, key):
            try:
                guard_age = time.time() - guard.stat().st_mtime
            except FileNotFoundError:
                return False
            if guard_age >= TAKEOVER_STALE_SECONDS:
                # A crashed takeover: clear it; the next check retries. A
                # duplicate in this double-crash case is acceptable.
                guard.unlink(missing_ok=True)
            return False
        try:
            try:
                age = time.time() - lock.stat().st_mtime
            except FileNotFoundError:
                return self._create_lock(lock, key)
            if age < self._lease:
                return False
            lock.unlink(missing_ok=True)
            return self._create_lock(lock, key)
        finally:
            guard.unlink(missing_ok=True)

    def _commit_file(self, key: str) -> None:
        lock, done = self._paths(key)
        done.write_text(f"{time.time()}\n{key}\n", encoding="utf-8")
        lock.unlink(missing_ok=True)

    def _release_file(self, key: str) -> None:
        self._paths(key)[0].unlink(missing_ok=True)

    # -- redis --------------------------------------------------------------

    def _redis_key(self, kind: str, key: str) -> str:
        ns = f"{self._namespace}:" if self._namespace else ""
        return f"{REDIS_KEY_PREFIX}{kind}:{ns}{key}"

    async def _claim_redis(self, key: str) -> bool:
        assert self._redis_factory is not None
        client = self._redis_factory()
        if await client.exists(self._redis_key("done", key)):
            return False
        created = await client.set(
            self._redis_key("claim", key), "1", nx=True, ex=self._lease
        )
        return bool(created)

    # -- public -------------------------------------------------------------

    async def claim(self, key: str) -> bool:
        """True when this process should announce ``key`` (lease taken)."""
        if self._redis_factory is not None:
            try:
                won = await self._claim_redis(key)
                if won:
                    self._via[key] = "redis"
                return won
            except Exception as exc:
                logger.warning(
                    "plugin_update_announce_redis_failed: %s", type(exc).__name__
                )
        try:
            won = self.claim_file(key)
        except OSError as exc:
            logger.warning("plugin_update_announce_lock_failed: %s", type(exc).__name__)
            self._via[key] = "none"
            return True
        if won:
            self._via[key] = "file"
        return won

    async def is_done(self, key: str) -> bool:
        """True when ``key`` was announced (a ``done`` marker exists)."""
        if self._redis_factory is not None:
            try:
                client = self._redis_factory()
                return bool(await client.exists(self._redis_key("done", key)))
            except Exception as exc:
                logger.warning(
                    "plugin_update_announce_redis_failed: %s", type(exc).__name__
                )
        return self._paths(key)[1].exists()

    async def commit(self, key: str) -> None:
        """Record that the event for ``key`` went out (long-lived marker)."""
        via = self._via.pop(key, "none")
        try:
            if via == "redis":
                client = self._redis_factory()  # type: ignore[misc]
                await client.set(self._redis_key("done", key), "1", ex=self._ttl)
                await client.delete(self._redis_key("claim", key))
            elif via == "file":
                self._commit_file(key)
        except Exception as exc:  # the lease expiring re-announces: acceptable
            logger.warning(
                "plugin_update_announce_commit_failed: %s", type(exc).__name__
            )

    async def release(self, key: str) -> None:
        """Drop the lease on ``key`` after a failed emit, so the next check retries."""
        via = self._via.pop(key, "none")
        try:
            if via == "redis":
                client = self._redis_factory()  # type: ignore[misc]
                await client.delete(self._redis_key("claim", key))
            elif via == "file":
                self._release_file(key)
        except Exception as exc:  # the lease expires on its own
            logger.warning(
                "plugin_update_announce_release_failed: %s", type(exc).__name__
            )


def _shared_redis_url() -> str:
    try:
        from core.config import get_storage_config

        storage = get_storage_config()
    except Exception:  # silent-ok: config unavailable, host-local lock only
        return ""
    if getattr(storage, "cache_backend", "") != "redis":
        return ""
    return str(getattr(storage, "cache_redis_url", "") or "")


_warned_empty_namespace = False


def _namespace(instance_id: str) -> str:
    """Deployment identity: explicit id, else the ``APP_BASE_URL`` host."""
    explicit = instance_id.strip()
    if explicit:
        return re.sub(r"[^A-Za-z0-9_.:-]", "_", explicit)
    host = urlsplit(os.getenv("APP_BASE_URL", "").strip()).hostname or ""
    return host.lower()


def build_announcement_gate(root: Path, instance_id: str = "") -> AnnouncementGate:
    """A gate on Redis when ``CACHE_BACKEND=redis`` with a URL, else on files.

    Args:
        root: The update cache directory.
        instance_id: ``PLUGIN_UPDATE_INSTANCE_ID``; falls back to the
            ``APP_BASE_URL`` host so deployments sharing a Redis stay apart.
    """
    global _warned_empty_namespace
    namespace = _namespace(instance_id)
    url = _shared_redis_url()
    if url and not namespace and not _warned_empty_namespace:
        _warned_empty_namespace = True
        logger.warning(
            "plugin_update_announce_namespace_empty: set "
            "PLUGIN_UPDATE_INSTANCE_ID when several deployments share this Redis"
        )
    if not url:
        return AnnouncementGate(root, namespace=namespace)

    def factory() -> Any:
        from core.cache.redis_cache import create_redis_client

        return create_redis_client(url)

    return AnnouncementGate(root, redis_factory=factory, namespace=namespace)


__all__ = [
    "ANNOUNCE_TTL_SECONDS",
    "LEASE_SECONDS",
    "TAKEOVER_STALE_SECONDS",
    "LOCK_DIRNAME",
    "REDIS_KEY_PREFIX",
    "AnnouncementGate",
    "build_announcement_gate",
]
