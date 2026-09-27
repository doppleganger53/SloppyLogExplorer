"""Sync helpers for copying log files from a source tree into a target tree.

The UI uses these functions to find files that are missing from the target
library or are newer in the source tree, then copy them while preserving the
original folder structure.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from .models import SyncCandidate
from .parser import LOG_EXTENSIONS


def discover_sync_candidates(source: str | Path, target: str | Path) -> list[SyncCandidate]:
    """Return log files under ``source`` that should be copied into ``target``."""
    source_root = Path(source).resolve()
    target_root = Path(target).resolve()
    if not source_root.is_dir():
        raise ValueError("The radio SD logs directory does not exist or is unavailable.")
    if target_root.exists() and not target_root.is_dir():
        raise ValueError("The PC library path must be a directory.")
    if source_root.is_relative_to(target_root) or target_root.is_relative_to(source_root):
        raise ValueError("Select separate, non-overlapping source and target directories.")
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
        if not source_file.resolve().is_relative_to(source_root):
            raise ValueError(f"A source link points outside the selected logs directory: {rel}")
        if not target_file.resolve().is_relative_to(target_root):
            raise ValueError(f"A target link points outside the selected PC library: {rel}")
        source_mtime = source_file.stat().st_mtime

        # A missing target file is always a copy candidate.
        if not target_file.exists():
            candidates.append(SyncCandidate(source_file, target_file, rel, "missing", source_mtime, None, source_root, target_root))
            continue

        if not target_file.is_file():
            raise ValueError(f"A directory blocks the destination log file: {rel}")
        target_mtime = target_file.stat().st_mtime

        # Use a one-second tolerance so filesystem timestamp granularity does
        # not create noisy "newer" results after copy operations.
        if source_mtime > target_mtime + 1:
            candidates.append(SyncCandidate(source_file, target_file, rel, "newer", source_mtime, target_mtime, source_root, target_root))

    return candidates


def copy_candidates(candidates: list[SyncCandidate]) -> int:
    """Copy each candidate into place and return the number of files copied."""
    copied = 0
    for candidate in candidates:
        _validate_candidate(candidate)
    for candidate in candidates:
        # Recreate the destination tree before copying so nested relative paths
        # are supported without requiring the caller to pre-create directories.
        candidate.target.parent.mkdir(parents=True, exist_ok=True)

        _validate_candidate(candidate)
        # Render to the destination filesystem, then replace atomically. A
        # disconnected SD card or failed copy cannot truncate an existing log.
        descriptor, temporary_name = tempfile.mkstemp(prefix=".sloppy-sync-", suffix=".tmp", dir=candidate.target.parent)
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            before = candidate.source.stat()
            shutil.copy2(candidate.source, temporary)
            after = candidate.source.stat()
            if before.st_mtime_ns != after.st_mtime_ns or before.st_size != after.st_size:
                raise ValueError(f"The source log changed while copying; scan again: {candidate.relative_path}")
            _validate_candidate(candidate)
            temporary.replace(candidate.target)
        finally:
            temporary.unlink(missing_ok=True)
        copied += 1
    return copied


def _validate_candidate(candidate: SyncCandidate) -> None:
    relative = candidate.relative_path
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError("The sync candidate has an invalid relative file path; scan again.")
    source_root = candidate.source_root or candidate.source.parents[len(relative.parts) - 1].resolve()
    target_root = candidate.target_root or candidate.target.parents[len(relative.parts) - 1].resolve()
    if not candidate.source.resolve().is_relative_to(source_root):
        raise ValueError(f"A source link points outside the selected logs directory: {relative}")
    if not candidate.target.resolve().is_relative_to(target_root):
        raise ValueError(f"A target link points outside the selected PC library: {relative}")
    if candidate.source.resolve() == candidate.target.resolve():
        raise ValueError("The source and destination log file must be different.")
    if candidate.source.stat().st_mtime != candidate.source_mtime:
        raise ValueError(f"The source log changed after scanning; scan again: {relative}")
    target_mtime = candidate.target.stat().st_mtime if candidate.target.exists() else None
    if target_mtime != candidate.target_mtime:
        raise ValueError(f"The destination changed after scanning; scan again: {relative}")
