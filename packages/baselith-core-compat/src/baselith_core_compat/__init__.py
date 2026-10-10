# ns-migrate: skip-file  (this package exists to keep the legacy names alive)
"""Legacy ``core`` / ``plugins`` import names for code written before 1.0.

BaselithCore 1.0 moved its implementation under one top-level package:
``core.*`` became ``baselith.core.*`` and ``plugins.*`` became
``baselith.plugins.*``. This optional distribution keeps the old spellings
importable — for plugins published before 1.0, for jobs already queued in
Redis with a ``core.…`` function path, and for code that has not been migrated
yet — by aliasing them to the new modules.

It is deliberately *not* part of ``baselith-core``: shipping a top-level
``core`` again would reintroduce the collision 1.0 removed (a project with its
own ``core`` package, such as a Django app). Nothing here creates a ``core``
directory; a meta-path finder answers for the names instead, and only when no
real ``core``/``plugins`` package is found first on ``sys.path`` — so a user
project's own ``core`` app still wins.

Activation is automatic through ``baselith_core_compat.pth``; call
:func:`install` yourself when the ``.pth`` hook is unavailable (``python -S``,
some frozen apps).
"""

from __future__ import annotations

from ._finder import LEGACY_PREFIXES, LegacyAliasFinder, install, uninstall

__all__ = ["LEGACY_PREFIXES", "LegacyAliasFinder", "install", "uninstall"]
__version__ = "1.0.0"
