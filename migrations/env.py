import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection

from core.config import get_storage_config
from core.observability.logging import ensure_configured

# Configure logging only — do NOT build the FastAPI app here.
#
# create_app() reconstructs the entire app (all middleware + plugins) AND wires
# the lifespan that calls ensure_schema() -> alembic `upgrade head`. Because these
# migrations are themselves invoked from that startup path
# (lifespan -> Postgres init -> ensure_schema -> command.upgrade -> this env.py),
# calling create_app() here re-entered app construction on every boot: workers
# never reached "startup complete" and span-looped (CPU storm with N workers, a
# ~20s restart loop with one). Config is loaded lazily by the pydantic settings
# getters (get_storage_config()), so migrations need no app object
# (target_metadata=None — migrations are hand-written SQL).
ensure_configured()

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def get_url() -> str:
    storage_config = get_storage_config()
    return storage_config.conninfo


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        dialect_name="postgresql",
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    url = get_url()
    # Ensure URL uses the async driver
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg_async://", 1)

    connectable = create_async_engine(url)

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
