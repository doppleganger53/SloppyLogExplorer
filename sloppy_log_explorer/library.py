from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .models import LogFileInfo
from .parser import LOG_EXTENSIONS, load_log


def scan_library(root: str | Path) -> list[LogFileInfo]:
    root_path = Path(root)
    logs: list[LogFileInfo] = []
    for path in sorted(root_path.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in LOG_EXTENSIONS:
            continue
        try:
            logs.append(load_log(path, root_path).info)
        except Exception:
            continue
    logs.sort(key=lambda item: (item.model.lower(), -item.modified, item.name.lower()))
    return logs


def group_by_model(logs: list[LogFileInfo]) -> dict[str, list[LogFileInfo]]:
    grouped: dict[str, list[LogFileInfo]] = defaultdict(list)
    for log in logs:
        grouped[log.model].append(log)
    return dict(sorted(grouped.items(), key=lambda pair: pair[0].lower()))

