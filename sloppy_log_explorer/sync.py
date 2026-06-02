from __future__ import annotations

import shutil
from pathlib import Path

from .models import SyncCandidate
from .parser import LOG_EXTENSIONS


def discover_sync_candidates(source: str | Path, target: str | Path) -> list[SyncCandidate]:
    source_root = Path(source)
    target_root = Path(target)
    candidates: list[SyncCandidate] = []
    for source_file in sorted(source_root.rglob("*")):
        if not source_file.is_file() or source_file.suffix.lower() not in LOG_EXTENSIONS:
            continue
        rel = source_file.relative_to(source_root)
        target_file = target_root / rel
        source_mtime = source_file.stat().st_mtime
        if not target_file.exists():
            candidates.append(SyncCandidate(source_file, target_file, rel, "missing", source_mtime, None))
            continue
        target_mtime = target_file.stat().st_mtime
        if source_mtime > target_mtime + 1:
            candidates.append(SyncCandidate(source_file, target_file, rel, "newer", source_mtime, target_mtime))
    return candidates


def copy_candidates(candidates: list[SyncCandidate]) -> int:
    copied = 0
    for candidate in candidates:
        candidate.target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidate.source, candidate.target)
        copied += 1
    return copied

