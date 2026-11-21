"""Metadata-only scanning and grouping for log library trees."""

from __future__ import annotations

import os
import stat
from collections import defaultdict
from pathlib import Path

from .models import LibraryLogInfo
from .parser import LOG_EXTENSIONS, _model_from_path


NONFLIGHT_ARCHIVE = "non-flight telemetry"
_ARCHIVE_NAME = os.path.normcase(NONFLIGHT_ARCHIVE)


def in_nonflight_archive(path: Path) -> bool:
    return any(os.path.normcase(part) == _ARCHIVE_NAME for part in path.parts)


def _archive_directory(parent: Path, name: str) -> bool:
    if os.path.normcase(name) == _ARCHIVE_NAME:
        return True
    # os.walk skips symbolic links by default, but traverses Windows junctions.
    # Resolve only junction targets, never the contents of ordinary cloud folders.
    if os.name == "nt":
        directory = parent / name
        try:
            if directory.lstat().st_reparse_tag == stat.IO_REPARSE_TAG_MOUNT_POINT:
                return in_nonflight_archive(directory.resolve())
        except OSError:
            pass
    return False


def scan_library(root: str | Path) -> list[LibraryLogInfo]:
    root_path = Path(root)
    logs: list[LibraryLogInfo] = []
    # Walk directories without opening file contents so large telemetry
    # libraries stay fast and the scan can be used for indexing.
    for dirpath, dirnames, filenames in os.walk(root_path):
        # Maintained libraries can also have an archive below their flight-log
        # directory. Explicitly opening an archive still lets the pilot browse it.
        current = Path(dirpath)
        dirnames[:] = sorted(name for name in dirnames if not _archive_directory(current, name))
        filenames.sort()
        for filename in filenames:
            path = current / filename
            if path.suffix.lower() not in LOG_EXTENSIONS:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            logs.append(
                LibraryLogInfo(
                    path=path,
                    model=_model_from_path(path, root_path),
                    name=path.name,
                    modified=stat.st_mtime,
                    size=stat.st_size,
                )
            )
    # Stable ordering keeps the sidebar predictable: model groups first, then
    # newest logs at the top of each group.
    logs.sort(key=lambda item: (item.model.lower(), -item.modified, item.name.lower()))
    return logs


def group_by_model(logs: list[LibraryLogInfo]) -> dict[str, list[LibraryLogInfo]]:
    grouped: dict[str, list[LibraryLogInfo]] = defaultdict(list)
    for log in logs:
        grouped[log.model].append(log)
    return dict(sorted(grouped.items(), key=lambda pair: pair[0].lower()))
