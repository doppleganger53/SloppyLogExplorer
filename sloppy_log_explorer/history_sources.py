"""Local reception sources shared by the site index, static HeatMap and history."""

from __future__ import annotations

import hashlib
import os
import re
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime
from lzma import LZMAError
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Any, BinaryIO
from zlib import error as DeflateError
from zipfile import BadZipFile, ZipFile, ZipInfo

from .library import _archive_directory, in_nonflight_archive
from .models import LibraryLogInfo, ReceptionLogRecord
from .parser import LOG_EXTENSIONS, _model_from_path, load_log
from .spektrum_history import recording_date, write_reception_csv


READ_ERRORS = (OSError, ValueError, BadZipFile, EOFError, RuntimeError, LZMAError, DeflateError)
_HISTORY_EXTENSIONS = LOG_EXTENSIONS | {".tlm"}
_ARCHIVE_FOLDERS = {"logs", "log", "telemetry", "csv", "sd", "models", "archive", "backup", "backups"}


class ScanCancelled(Exception):
    """Abandon an incomplete scan without publishing partial records."""


class SourceUnavailable(ValueError):
    def __init__(self, status: str):
        super().__init__(status)
        self.status = status


@dataclass(frozen=True)
class RecordingSource:
    path: Path
    name: str
    model: str
    size: int
    mtime_ns: int
    modified: float
    member: ZipInfo | None = None
    error: str = ""

    def verify_fingerprint(self, record: ReceptionLogRecord) -> None:
        if (self.size, self.mtime_ns) != (record.file_size, record.mtime_ns):
            raise SourceUnavailable("changed_since_index")


class RecordingReader:
    """Hold at most one ZIP directory open while processing its recordings."""

    def __init__(self, root: Path, is_cancelled: Callable[[], bool] | None = None):
        self.root = root
        self.is_cancelled = is_cancelled
        self.archive: ZipFile | None = None
        self.archive_path: Path | None = None

    def check_cancelled(self) -> None:
        if self.is_cancelled is not None and self.is_cancelled():
            raise ScanCancelled

    def close(self) -> None:
        if self.archive is not None:
            self.archive.close()
        self.archive = None
        self.archive_path = None

    def _zip(self, path: Path) -> ZipFile:
        if self.archive is None or path != self.archive_path:
            self.close()
            self.archive = ZipFile(path)
            self.archive_path = path
        return self.archive

    def _member(self, archive: Path, number: int, info: ZipInfo, mtime_ns: int) -> RecordingSource:
        # ZIP entries can share a name, differ only in case or contain arbitrary
        # path components. Persist an ordinal, and never extract by member name.
        logical = PurePosixPath(info.filename.replace("\\", "/"))
        path = archive / f"member-{number}{logical.suffix.lower()}"
        # Zip folders often wrap the aircraft folder in Logs or Backups. Use the
        # library's normal aircraft rule after discarding those wrapper names.
        outer = archive.parent.relative_to(self.root).parts
        parents = [p for p in (*outer, *logical.parts[:-1])
                   if p.casefold() not in _ARCHIVE_FOLDERS and p not in {".", "..", "/"}]
        model = parents[0] if parents else _model_from_path(self.root / logical.name, self.root)
        try:
            modified = datetime(*info.date_time).timestamp()
        except (ValueError, OSError, OverflowError):
            modified = mtime_ns / 1_000_000_000
        return RecordingSource(path, logical.name, model, info.file_size, mtime_ns, modified, info)

    def lookup(self, path: Path, fallback: LibraryLogInfo | None = None) -> RecordingSource:
        match = re.fullmatch(r"member-([0-9]+)\.(?:csv|log|tlm)", path.name, re.I)
        if path.parent.suffix.lower() == ".zip" and match and not path.parent.is_dir():
            number = int(match[1])
            items = self._zip(path.parent).infolist()
            if number >= len(items):
                raise OSError("The archive member is no longer present")
            source = self._member(path.parent, number, items[number], path.parent.stat().st_mtime_ns)
            if source.path != path:
                raise OSError("The archive member has changed")
            return source
        try:
            stat = path.stat()
            size, modified_ns, modified = stat.st_size, stat.st_mtime_ns, stat.st_mtime
        except OSError:
            if fallback is None:
                raise
            size, modified = fallback.size, fallback.modified
            modified_ns = int(modified * 1_000_000_000)
        model = fallback.model if fallback else _model_from_path(path.resolve(), self.root.resolve())
        return RecordingSource(path, path.name, model, size, modified_ns, modified)

    def inventory(self, logs: Iterable[LibraryLogInfo]) -> list[RecordingSource]:
        sources = []
        for log in logs:
            self.check_cancelled()
            sources.append(self.lookup(log.path, log))
        for directory, folders, names in os.walk(self.root):
            self.check_cancelled()
            parent = Path(directory)
            folders[:] = [name for name in folders if not _archive_directory(parent, name)]
            for name in names:
                path = parent / name
                if path.suffix.lower() not in {".zip", ".tlm"}:
                    continue
                try:
                    source = self.lookup(path)
                    if path.suffix.lower() == ".tlm":
                        sources.append(source)
                        continue
                    for number, info in enumerate(self._zip(path).infolist()):
                        self.check_cancelled()
                        logical = Path(info.filename.replace("\\", "/"))
                        if (not info.is_dir() and logical.suffix.lower() in _HISTORY_EXTENSIONS
                                and not in_nonflight_archive(logical)):
                            sources.append(self._member(path, number, info, source.mtime_ns))
                except (OSError, ValueError, BadZipFile) as exc:
                    # A corrupt or unavailable archive must not hide other sites.
                    try:
                        stat = path.stat()
                        size, ns, modified = stat.st_size, stat.st_mtime_ns, stat.st_mtime
                    except OSError:
                        size, ns, modified = 0, 0, 0.0
                    sources.append(RecordingSource(path, name, "", size, ns, modified, error=str(exc)))
        return sorted(sources, key=lambda item: (item.member is not None, os.path.normcase(str(item.path))))

    def open_bytes(self, source: RecordingSource) -> BinaryIO:
        if source.member is not None:
            return self._zip(source.path.parent).open(source.member)
        return source.path.open("rb")

    def digest(self, source: RecordingSource) -> str:
        digest = hashlib.sha256()
        with self.open_bytes(source) as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                self.check_cancelled()
                digest.update(block)
        return digest.hexdigest()

    @contextmanager
    def csv(self, source: RecordingSource) -> Iterator[tuple[Path, str, str]]:
        if source.member is None and source.path.suffix.lower() in LOG_EXTENSIONS:
            yield source.path, source.model, ""
            return
        with TemporaryDirectory(prefix="sle-reception-") as directory:
            path = Path(directory) / "recording.csv"
            model, date_source = source.model, ""
            with self.open_bytes(source) as stream:
                if source.path.suffix.lower() == ".tlm":
                    day, date_source = recording_date(source.name, source.modified)
                    status, header_model = write_reception_csv(stream, path, day, self.check_cancelled)
                    if status != "ok":
                        raise SourceUnavailable(status)
                    model = header_model or model
                else:
                    with path.open("wb") as output:
                        for block in iter(lambda: stream.read(1024 * 1024), b""):
                            self.check_cancelled()
                            output.write(block)
            # Let the normal CSV index use the member date for its mtime fallback.
            os.utime(path, (source.modified, source.modified))
            yield path, model, date_source


def build_site_history(
    records: Iterable[ReceptionLogRecord | Mapping[str, Any]],
    site_center: tuple[float, float],
    progress: Callable[[int, int, str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> dict:
    from .history_cells import bin_history_recording
    from .reception import _coerce_record

    selected = sorted((_coerce_record(record) for record in records), key=lambda r: r.file_path)
    logs = []
    counts: Counter[str] = Counter()
    if selected:
        with closing(RecordingReader(selected[0].library_root, is_cancelled)) as reader:
            for index, record in enumerate(selected, 1):
                reader.check_cancelled()
                if record.status != "ok":
                    counts[record.status] += 1
                    continue
                name = record.file_path.name
                try:
                    source = reader.lookup(record.file_path)
                    name = source.name
                    source.verify_fingerprint(record)
                    with reader.csv(source) as (path, model, date_source):
                        log = load_log(path, reader.root)
                        grids = bin_history_recording(log, site_center)
                    if not grids["5"]:
                        raise SourceUnavailable("no_paired_reception")
                    logs.append({
                        "date": record.flight_date.isoformat(), "model": model,
                        "inferred": record.date_inferred, "grids": grids,
                        "dateSource": date_source or ("mtime" if record.date_inferred else "recorded"),
                    })
                    counts["included"] += 1
                except SourceUnavailable as exc:
                    counts[exc.status] += 1
                except READ_ERRORS:
                    counts["read_error"] += 1
                if progress:
                    progress(index, len(selected), name)
    logs.sort(key=lambda log: (log["date"], log["model"]))
    return {"logs": logs, "counts": dict(counts)}
