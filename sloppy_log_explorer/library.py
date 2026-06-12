"""Metadata-only scanning and grouping for log library trees."""

from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path

from .models import LibraryLogInfo
from .parser import LOG_EXTENSIONS, _model_from_path


def scan_library(root: str | Path) -> list[LibraryLogInfo]:
    root_path = Path(root)
    logs: list[LibraryLogInfo] = []
    # Walk directories without opening file contents so large telemetry
    # libraries stay fast and the scan can be used for indexing.
    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames.sort()
        filenames.sort()
        current = Path(dirpath)
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
