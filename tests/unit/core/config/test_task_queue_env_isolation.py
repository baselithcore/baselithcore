"""TaskQueueConfig must only read its own, namespaced environment variables.

The config used to declare ``env_prefix=""``, so every field bound to a bare,
generic name: ``REDIS_URL`` fed ``redis_url``, ``MAX_CONNECTIONS`` fed
``max_connections``, and so on. Any deployment (or CI job) that exported one of
those generic names for an unrelated service silently redirected the broker,
enqueueing jobs into a database no worker listens on.
"""

from core.config.task_queue import TaskQueueConfig


class TestEnvIsolation:
    """Generic environment names must not reach the queue configuration."""

    def test_bare_redis_url_does_not_redirect_the_broker(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://unrelated:6379/0")
        monkeypatch.delenv("QUEUE_REDIS_URL", raising=False)
        monkeypatch.delenv("TASK_QUEUE_REDIS_URL", raising=False)

        cfg = TaskQueueConfig()

        assert cfg.redis_url is None
        assert cfg.get_redis_url() == "redis://localhost:6379/2"

    def test_explicit_queue_url_wins_over_ambient_redis_url(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://unrelated:6379/0")

        cfg = TaskQueueConfig(queue_redis_url="redis://queue-host:6379/7")

        assert cfg.get_redis_url() == "redis://queue-host:6379/7"

    def test_bare_generic_names_do_not_bind_scalar_fields(self, monkeypatch):
        monkeypatch.setenv("MAX_CONNECTIONS", "1")
        monkeypatch.setenv("JOB_TIMEOUT", "1")
        monkeypatch.setenv("DEFAULT_QUEUE", "hijacked")

        cfg = TaskQueueConfig()

        assert cfg.max_connections == 50
        assert cfg.job_timeout == 3600
        assert cfg.default_queue == "default"


class TestSupportedEnvNames:
    """The documented and prefixed names must keep working."""

    def test_documented_queue_redis_url_is_honoured(self, monkeypatch):
        monkeypatch.delenv("TASK_QUEUE_REDIS_URL", raising=False)
        monkeypatch.setenv("QUEUE_REDIS_URL", "redis://documented:6379/2")

        assert TaskQueueConfig().get_redis_url() == "redis://documented:6379/2"

    def test_prefixed_name_overrides_the_generic_queue_name(self, monkeypatch):
        monkeypatch.setenv("QUEUE_REDIS_URL", "redis://documented:6379/2")
        monkeypatch.setenv("TASK_QUEUE_REDIS_URL", "redis://prefixed:6379/3")

        assert TaskQueueConfig().get_redis_url() == "redis://prefixed:6379/3"

    def test_prefixed_scalars_bind(self, monkeypatch):
        monkeypatch.setenv("TASK_QUEUE_MAX_CONNECTIONS", "7")
        monkeypatch.setenv("TASK_QUEUE_DEFAULT_QUEUE", "urgent")

        cfg = TaskQueueConfig()

        assert cfg.max_connections == 7
        assert cfg.default_queue == "urgent"
