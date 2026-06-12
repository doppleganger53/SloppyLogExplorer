"""Sync helpers for copying log files from a source tree into a target tree.

The UI uses these functions to find files that are missing from the target
library or are newer in the source tree, then copy them while preserving the
original folder structure.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .models import SyncCandidate
from .parser import LOG_EXTENSIONS


def discover_sync_candidates(source: str | Path, target: str | Path) -> list[SyncCandidate]:
    """Return log files under ``source`` that should be copied into ``target``."""
    source_root = Path(source)
    target_root = Path(target)
    candidates: list[SyncCandidate] = []

    # Walk recursively so nested log folders are handled without special cases.
    # Sorting keeps the output stable for the UI and for tests.
    for source_file in sorted(source_root.rglob("*")):
        # Ignore directories and non-log files; sync only deals with telemetry
        # artifacts that the parser knows how to read.
        if not source_file.is_file() or source_file.suffix.lower() not in LOG_EXTENSIONS:
            continue

        # Preserve the relative structure from the source tree when mirroring
        # files into the target library.
        rel = source_file.relative_to(source_root)
        target_file = target_root / rel
        source_mtime = source_file.stat().st_mtime

        # A missing target file is always a copy candidate.
        if not target_file.exists():
            candidates.append(SyncCandidate(source_file, target_file, rel, "missing", source_mtime, None))
            continue

        target_mtime = target_file.stat().st_mtime

        # Use a one-second tolerance so filesystem timestamp granularity does
        # not create noisy "newer" results after copy operations.
        if source_mtime > target_mtime + 1:
            candidates.append(SyncCandidate(source_file, target_file, rel, "newer", source_mtime, target_mtime))

    return candidates


def copy_candidates(candidates: list[SyncCandidate]) -> int:
    """Copy each candidate into place and return the number of files copied."""
    copied = 0
    for candidate in candidates:
        # Recreate the destination tree before copying so nested relative paths
        # are supported without requiring the caller to pre-create directories.
        candidate.target.parent.mkdir(parents=True, exist_ok=True)

        # copy2 preserves timestamps and other metadata, which keeps later sync
        # comparisons and file provenance consistent.
        shutil.copy2(candidate.source, candidate.target)
        copied += 1
    return copied
