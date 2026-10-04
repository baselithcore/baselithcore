"""Apply the packaged migrations: ``python -m core.db.migrate``.

``baselith db migrate`` runs this module in a child process so the Alembic
run — which configures logging and starts its own event loop in ``env.py`` —
stays isolated from the CLI, and its output can be captured verbatim for
``--json``. Unlike the bare ``alembic`` CLI it needs no ``alembic.ini`` in the
working directory and takes the same advisory lock as the startup upgrade.
"""

from __future__ import annotations

import sys


def main() -> int:
    """Upgrade the configured database to the packaged head revision.

    Returns:
        ``0`` on success, ``1`` when the migration scripts cannot be located.
    """
    from core.db.migration_config import MigrationsNotFoundError
    from core.db.schema import upgrade_head

    try:
        upgrade_head()
    except MigrationsNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
