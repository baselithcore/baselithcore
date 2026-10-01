"""Scratch overlays: look at a store entry as the loader would, live link untouched.

A scratch overlay is a temporary directory inside ``<overlay>/.store`` holding
``.store -> ..`` and ``<plugin> -> .store/<target>``, so an entry is judged
(or migrated) under the plugin's own name while ``<overlay>/<plugin>`` — the
link the running API resolves — still points where it did. Its name starts
with ``.staging-<run id>-``, so reconciliation and the stale-scratch prune
remove it when a crash leaves it behind.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from core.plugins.overlay import STORE_DIRNAME, candidate_overlay_dirs

from .swap import current_target


def make_scratch_overlay(
    overlay: Path,
    *,
    run_id: str,
    label: str,
    plugin: str,
    target: str,
    with_others: bool = False,
) -> Path:
    """Create a scratch overlay where ``plugin`` resolves to ``.store/<target>``.

    Args:
        overlay: The live overlay directory.
        run_id: The run the scratch belongs to (part of its name).
        label: What the scratch is for (part of its name).
        plugin: The plugin linked to ``target``.
        target: A store entry name (a single path component).
        with_others: Also link every other plugin the live overlay links into
            the store, so a process pointed at the scratch sees the overlay as
            it will be once ``plugin`` is switched.

    Returns:
        The scratch directory; the caller removes it (``shutil.rmtree``).
    """
    store = overlay / STORE_DIRNAME
    scratch = Path(tempfile.mkdtemp(prefix=f".staging-{run_id}-{label}-", dir=store))
    try:
        os.symlink(os.pardir, scratch / STORE_DIRNAME)
        if with_others:
            for entry in candidate_overlay_dirs(overlay):
                if entry.name == plugin or not entry.is_symlink():
                    continue
                try:
                    linked = current_target(overlay, entry.name)
                except ValueError:  # the loader refuses it too
                    continue
                if linked is not None:
                    os.symlink(
                        os.path.join(STORE_DIRNAME, linked), scratch / entry.name
                    )
        os.symlink(os.path.join(STORE_DIRNAME, target), scratch / plugin)
    except BaseException:
        shutil.rmtree(scratch, ignore_errors=True)
        raise
    return scratch


__all__ = ["make_scratch_overlay"]
