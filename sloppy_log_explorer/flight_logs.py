"""Reviewed, in-process flight-log generation for the desktop application.

The detector is bundled separately; this module owns preview validation, local
settings, append/rebuild policy and atomic writes. A preview is the only input
to generation, so accepting a candidate never implicitly approves a rebuild.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import json
import os
import re
import shutil
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import _flight_log_engine as engine
from .library import NONFLIGHT_ARCHIVE, in_nonflight_archive


DEFAULT_POLICY: dict[str, Any] = {
    "max_sensors": [],
    "exclude_sensors": [],
    "auto_vfr": True,
    "auto_temperatures": True,
    "auto_servo_currents": True,
}
DETECTION_POLICY = {
    "bucket_seconds": 1,
    "rolling_seconds": 12,
    "merge_gap_seconds": 45,
    "min_flight_seconds": 60,
}


def infer_aircraft(path: str | Path, library_root: str | Path | None = None) -> str:
    """Filename timestamp prefix takes priority over the first relative folder."""
    return engine.infer_plane(Path(path).resolve(), Path(library_root).resolve() if library_root is not None else None)


def _fingerprint(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_aircraft(name: str) -> str:
    if not isinstance(name, str) or not name.strip() or "\n" in name or "\r" in name:
        raise ValueError("Aircraft names must be nonempty single-line strings")
    return name.strip()


def _validate_policy(policy: Mapping[str, Any]) -> None:
    for key in ("max_sensors", "exclude_sensors"):
        if key in policy and (
            not isinstance(policy[key], list)
            or any(not isinstance(value, str) or not value.strip() for value in policy[key])
        ):
            raise ValueError(f"{key} must be a list of exact, nonempty telemetry headers")
    for key in ("auto_vfr", "auto_temperatures", "auto_servo_currents"):
        if key in policy and not isinstance(policy[key], bool):
            raise ValueError(f"{key} must be true or false")


def _validated_settings(settings: Mapping[str, Any] | None) -> dict[str, Any]:
    result = copy.deepcopy(dict(settings or {}))
    for section in ("defaults", "planes"):
        if not isinstance(result.setdefault(section, {}), dict):
            raise ValueError(f"Flight-log settings {section} must be an object")
    _validate_policy(result["defaults"])
    seen: set[str] = set()
    for aircraft, policy in result["planes"].items():
        name = _validate_aircraft(aircraft)
        if name.casefold() in seen:
            raise ValueError(f"Duplicate case-insensitive aircraft settings: {aircraft}")
        seen.add(name.casefold())
        if not isinstance(policy, dict):
            raise ValueError(f"Telemetry settings for {aircraft} must be an object")
        _validate_policy(policy)
    orders = result.get("column_orders", {})
    if not isinstance(orders, dict):
        raise ValueError("Column orders must be an object keyed by aircraft")
    for aircraft, headers in orders.items():
        _validate_aircraft(aircraft)
        if not isinstance(headers, list) or any(not isinstance(header, str) or not header.strip() for header in headers) or len(set(headers)) != len(headers):
            raise ValueError("Column orders must contain unique, nonempty column names")
    return result


def effective_policy(settings: Mapping[str, Any], aircraft: str) -> dict[str, Any]:
    """Per-aircraft keys override defaults; an absent key inherits its default."""
    checked = _validated_settings(settings)
    result = copy.deepcopy(DEFAULT_POLICY)
    result.update({key: value for key, value in checked["defaults"].items() if key in DEFAULT_POLICY})
    override = next((value for name, value in checked["planes"].items()
                     if name.strip().casefold() == aircraft.strip().casefold()), {})
    result.update({key: value for key, value in override.items() if key in DEFAULT_POLICY})
    # Exclusions win even when a requested maximum appears in both lists.
    excluded = {engine.normalize_header(value) for value in result["exclude_sensors"]}
    result["max_sensors"] = list(dict.fromkeys(
        value for value in result["max_sensors"] if engine.normalize_header(value) not in excluded
    ))
    return result


@dataclass(frozen=True)
class FlightLogDocument:
    aircraft: str
    path: Path
    headers: list[str]
    rows: list[list[str]]
    text: str
    fingerprint: str


@dataclass(frozen=True)
class FlightSource:
    path: Path
    start: datetime
    end: datetime

    @staticmethod
    def _plot_time(stamp: datetime) -> datetime:
        # The telemetry parser converts offset-bearing timestamps to naive UTC.
        return stamp.astimezone(timezone.utc).replace(tzinfo=None) if stamp.tzinfo is not None else stamp

    @classmethod
    def windows(cls, analysis: engine.FileAnalysis) -> dict[tuple[str, str], FlightSource]:
        return {(flight.display_datetime, flight.display_duration):
                    cls(analysis.path, cls._plot_time(flight.start), cls._plot_time(flight.end))
                for flight in analysis.flights}


@dataclass(frozen=True)
class _SourceIndex:
    stat: os.stat_result
    fingerprint: str
    windows: dict[tuple[str, str], FlightSource]


@dataclass
class FlightLogPreview:
    report: dict[str, Any]
    _service_root: Path = field(repr=False)
    _flights: dict[str, engine.Flight] = field(repr=False)
    _source_fingerprints: dict[Path, str | None] = field(repr=False)
    _output_fingerprints: dict[Path, str | None] = field(repr=False)
    _output_texts: dict[Path, str | None] = field(repr=False)
    _output_paths: dict[str, Path] = field(repr=False)
    _settings: dict[str, Any] = field(repr=False)
    _rebuild: bool = field(repr=False)
    _archive_files: dict[Path, Path] = field(default_factory=dict, repr=False)
    _plans: dict[Path, str] = field(default_factory=dict, repr=False)

    @property
    def candidates(self) -> list[dict[str, Any]]:
        return [candidate for file in self.report["files"] for candidate in file["flights"]]

    @property
    def updates(self) -> list[dict[str, Any]]:
        return self.report["updates"]

    @property
    def pending_candidates(self) -> list[str]:
        return self.report["pending_candidates"]

    @property
    def can_generate(self) -> bool:
        return not self.report["writes_blocked"] and bool(self._plans)

    @property
    def rebuild(self) -> bool:
        return self._rebuild

    def archivable_sources(self, root: Path) -> list[Path]:
        root = root.resolve()
        return [path for path in self._archive_files if path.is_relative_to(root) and not in_nonflight_archive(path)]


def _read_headers(path: Path) -> list[str]:
    reader, handle = engine.open_csv(path)
    try:
        return engine.deduplicate_headers(next(reader, []))
    finally:
        handle.close()


def _table_cells(text: str) -> tuple[list[str], list[list[str]]]:
    lines = text.splitlines()
    index = next((i for i, line in enumerate(lines) if engine.schema_from_header(line) is not None), None)
    if index is None:
        raise ValueError("The document does not contain a supported flight-log table")
    headers = engine.split_markdown_row(lines[index])
    rows = []
    for line in lines[index + 2:]:
        if not line.strip().startswith("|"):
            break
        rows.append(engine.split_markdown_row(line))
    return headers, rows


def _flight_key(flight: engine.Flight) -> tuple[datetime, str]:
    return datetime.strptime(flight.display_datetime, "%Y-%m-%d %H:%M:%S"), flight.display_duration


def flight_end(start: datetime, duration: str) -> datetime:
    hours, minutes, seconds = map(int, duration.split(":"))
    return start + timedelta(hours=hours, minutes=minutes, seconds=seconds)


def _schema_changes(before: Sequence[str], after: Sequence[str]) -> dict[str, list[str]]:
    return {"before": list(before), "after": list(after),
            "added": [column for column in after if column not in before],
            "removed": [column for column in before if column not in after]}


class FlightLogService:
    def __init__(self, output_dir: str | Path):
        self.output_dir = Path(output_dir).expanduser().resolve()
        self._source_windows: dict[Path, _SourceIndex] = {}

    def find_flight_sources(self, file_aircraft: Mapping[str | Path, str], aircraft: str,
                            start: str, duration: str) -> list[FlightSource]:
        """Match a maintained row to actual detected windows, including older Markdown tables."""
        matches: dict[Path, FlightSource] = {}
        key = (start, duration)
        requested_start = datetime.fromisoformat(start)
        for source, name in file_aircraft.items():
            if name.casefold() != aircraft.casefold():
                continue
            path = Path(source).resolve()
            try:
                before = path.stat()
                cached = self._source_windows.get(path)
                if (cached is None or not self._same_source(cached.stat, before)
                        or (key in cached.windows and _fingerprint(path) != cached.fingerprint)):
                    if not engine.contains_flight_start(path, requested_start):
                        continue
                    digest = _fingerprint(path)
                    if digest is None:
                        continue
                    analysis = engine.analyze_file(path, plane_override=aircraft, scan_root=None,
                                                   **DETECTION_POLICY)
                    if (analysis.status == "error" or not self._same_source(before, path.stat())
                            or _fingerprint(path) != digest):
                        continue
                    cached = _SourceIndex(before, digest, FlightSource.windows(analysis))
                    self._source_windows[path] = cached
            except (OSError, ValueError, csv.Error):
                continue
            if key in cached.windows:
                matches[path] = cached.windows[key]
        return [matches[path] for path in sorted(matches)]

    @staticmethod
    def _same_source(first: os.stat_result, second: os.stat_result) -> bool:
        return (os.path.samestat(first, second) and first.st_mtime_ns == second.st_mtime_ns
                and first.st_ctime_ns == second.st_ctime_ns and first.st_size == second.st_size)

    @property
    def settings_path(self) -> Path:
        return self.output_dir / ".flight-log-settings.json"

    def load_settings(self) -> dict[str, Any]:
        if not self.settings_path.exists():
            return {"defaults": copy.deepcopy(DEFAULT_POLICY), "planes": {}}
        data = json.loads(self.settings_path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("Flight-log settings must be an object")
        return _validated_settings(data)

    def save_settings(self, settings: Mapping[str, Any]) -> dict[str, Any]:
        # Merge outer and per-aircraft metadata instead of dropping older skill
        # settings or unrelated future settings when a UI edits known fields.
        fingerprint = _fingerprint(self.settings_path)
        existing = self.load_settings()
        incoming = _validated_settings(settings)
        merged = {**existing, **incoming}
        merged["defaults"] = {**existing.get("defaults", {}), **incoming["defaults"]}
        # This is a complete policy edit: omitted known overrides reset to
        # inheritance. Unrecognized metadata remains available to other clients.
        planes = {name: {key: value for key, value in policy.items() if key not in DEFAULT_POLICY}
                  for name, policy in existing.get("planes", {}).items()}
        planes = {name: policy for name, policy in planes.items() if policy}
        for name, policy in incoming["planes"].items():
            old_name = next((key for key in existing.get("planes", {})
                             if key.strip().casefold() == name.strip().casefold()), name)
            unknown = {key: value for key, value in planes.get(old_name, {}).items() if key not in DEFAULT_POLICY}
            planes[old_name] = {**unknown, **policy}
        merged["planes"] = planes
        checked = _validated_settings(merged)
        self._atomic_write(self.settings_path, json.dumps(checked, indent=2, ensure_ascii=False) + "\n",
                           expected_fingerprint=fingerprint)
        return checked

    def list_logs(self) -> dict[str, Path]:
        result: dict[str, Path] = {}
        names: set[str] = set()
        if not self.output_dir.exists():
            return result
        for path in sorted(self.output_dir.glob("*-flight-log.md"), key=lambda item: item.name.casefold()):
            with path.open(encoding="utf-8-sig") as handle:
                heading = handle.readline().strip()
            match = re.fullmatch(r"# (.+) Flight Log", heading)
            name = match.group(1) if match else path.name[:-len("-flight-log.md")]
            if name.casefold() in names:
                raise ValueError(f"Multiple local logs claim aircraft {name!r}; resolve the duplicate before updating")
            names.add(name.casefold())
            result[name] = path
        return result

    def list_aircraft(self) -> list[str]:
        return list(self.list_logs())

    def _path_for(self, aircraft: str) -> Path:
        aircraft = _validate_aircraft(aircraft)
        known = self.list_logs()
        return next((path for name, path in known.items() if name.casefold() == aircraft.casefold()),
                    self.output_dir / f"{engine.slugify_plane(aircraft)}-flight-log.md")

    def available_sensors(
        self, file_aircraft: Mapping[str | Path, str], selected_aircraft: Sequence[str] | None = None,
    ) -> dict[str, list[str]]:
        selected = None if selected_aircraft is None else {name.casefold() for name in selected_aircraft}
        found: dict[str, list[str]] = defaultdict(list)
        for source, aircraft in file_aircraft.items():
            if selected is not None and aircraft.casefold() not in selected:
                continue
            for header in _read_headers(Path(source)):
                if header not in found[aircraft]:
                    found[aircraft].append(header)
        return dict(found)

    def preview(
        self, file_aircraft: Mapping[str | Path, str], selected_aircraft: Sequence[str] | None = None,
        settings: Mapping[str, Any] | None = None, decisions: Mapping[str, str] | None = None,
        rebuild: bool = False,
    ) -> FlightLogPreview:
        checked_settings = _validated_settings(self.load_settings() if settings is None else settings)
        selected = None if selected_aircraft is None else {
            _validate_aircraft(name).casefold() for name in selected_aircraft
        }
        canonical = {name.casefold(): name for name in self.list_aircraft()}
        mapping: dict[Path, str] = {}
        linked_sources: set[Path] = set()
        for source, raw_aircraft in file_aircraft.items():
            aircraft = _validate_aircraft(raw_aircraft)
            if selected is not None and aircraft.casefold() not in selected:
                continue
            aircraft = canonical.setdefault(aircraft.casefold(), aircraft)
            path = Path(source).expanduser().resolve()
            if path != Path(source).expanduser().absolute():
                linked_sources.add(path)
            if path in mapping and mapping[path] != aircraft:
                raise ValueError(f"Telemetry file is mapped to multiple aircraft: {path}")
            mapping[path] = aircraft
        owners: dict[str, str] = {path.name.casefold(): aircraft for aircraft, path in self.list_logs().items()}
        outputs: dict[Path, str | None] = {}
        output_texts: dict[Path, str | None] = {}
        output_paths: dict[str, Path] = {}
        logged_intervals: dict[str, list[tuple[datetime, datetime]]] = {}
        for aircraft in sorted(set(mapping.values()), key=str.casefold):
            output = self._path_for(aircraft)
            owner = owners.get(output.name.casefold())
            if owner is not None and owner.casefold() != aircraft.casefold():
                raise ValueError(f"Aircraft {owner!r} and {aircraft!r} map to the same output filename")
            owners[output.name.casefold()] = aircraft
            output_paths[aircraft] = output
            outputs[output] = _fingerprint(output)
            if output.exists():
                with output.open(encoding="utf-8", newline="") as handle:
                    output_texts[output] = handle.read()
            else:
                output_texts[output] = None
            if outputs[output] != _fingerprint(output):
                raise ValueError(f"Local log changed during preview: {output.name}; preview again")
            existing = output_texts[output]
            try:
                rows = _parse_snapshot(existing, output.name)[0] if existing is not None else []
                logged_intervals[aircraft] = [(row.timestamp, flight_end(row.timestamp, row.duration)) for row in rows]
            except (ValueError, OverflowError):
                # An unreadable maintained table cannot establish that a recording is unused.
                pass

        sources: dict[Path, str | None] = {}
        candidates: dict[str, engine.Flight] = {}
        archive_files: dict[Path, Path] = {}
        files: list[dict[str, Any]] = []
        available: dict[str, list[str]] = defaultdict(list)
        for path, aircraft in sorted(mapping.items(), key=lambda item: str(item[0]).casefold()):
            policy = effective_policy(checked_settings, aircraft)
            file_report: dict[str, Any] = {"path": str(path), "plane": aircraft, "aircraft": aircraft,
                                           "flights": [], "rejected_candidates": [], "telemetry_headers": []}
            try:
                if path.suffix.lower() not in engine.SUPPORTED_EXTENSIONS:
                    raise ValueError("Supported telemetry files are .csv and .log")
                sources[path] = _fingerprint(path)
                headers = _read_headers(path)
                file_report["telemetry_headers"] = headers
                for header in headers:
                    if header not in available[aircraft]:
                        available[aircraft].append(header)
                analysis = engine.analyze_file(
                    path, plane_override=aircraft, scan_root=None, **DETECTION_POLICY,
                    max_sensors=policy["max_sensors"], exclude_sensors=policy["exclude_sensors"],
                    auto_vfr=policy["auto_vfr"], auto_temperatures=policy["auto_temperatures"],
                    auto_servo_currents=policy["auto_servo_currents"],
                )
                if sources[path] != _fingerprint(path):
                    raise ValueError("Telemetry changed during analysis; preview again")
                digest = sources[path]
                if analysis.status != "error" and digest is not None:
                    self._source_windows[path] = _SourceIndex(path.stat(), digest, FlightSource.windows(analysis))
                file_report.update(status=analysis.status, detail=analysis.detail,
                                   rejected_candidates=analysis.rejected_candidates)
                if analysis.archive_interval and aircraft in logged_intervals and path not in linked_sources:
                    first, last = (stamp.replace(tzinfo=None) for stamp in analysis.archive_interval)
                    if not any(first <= end and last >= start for start, end in logged_intervals[aircraft]):
                        archive_files[path] = output_paths[aircraft]
                # Servo capture changes metrics only. Keep the existing review
                # identity so an added default never undoes a pilot decision.
                decision_policy = {key: value for key, value in policy.items() if key != "auto_servo_currents"}
                for flight in analysis.flights:
                    identity = [sources[path], aircraft.casefold(), flight.start.isoformat(), flight.end.isoformat(),
                                DETECTION_POLICY, decision_policy]
                    candidate_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
                    candidates[candidate_id] = flight
                    metrics = {engine.VFR_LABELS[family]: value for family, value in flight.vfr_minimums.items()
                               if family in (flight.vfr_present or ())}
                    metrics.update({f"Maximum {header}": value for header, value in
                                    {**flight.temperature_maximums, **flight.sensor_maximums}.items()})
                    file_report["flights"].append({
                        "candidate_id": candidate_id, "id": candidate_id, "plane": aircraft, "aircraft": aircraft,
                        "source": str(path), "start": flight.display_datetime, "duration": flight.display_duration,
                        "confidence": flight.confidence, "evidence": flight.evidence, "notes": flight.notes,
                        "vfr_minimums": flight.vfr_minimums, "vfr_present": flight.vfr_present,
                        "temperature_maximums": flight.temperature_maximums,
                        "sensor_maximums": flight.sensor_maximums, "metrics": metrics,
                    })
            except (OSError, ValueError, UnicodeError, csv.Error) as exc:
                file_report.update(status="error", detail=str(exc), flights=[])
            files.append(file_report)
        preview = FlightLogPreview({"files": files, "available_headers": dict(available),
                                    "files_scanned": len(files), "dry_run": True, "rebuild": bool(rebuild)},
                                   self.output_dir, candidates, sources, outputs, output_texts, output_paths,
                                   checked_settings, bool(rebuild), _archive_files=archive_files)
        return self.with_decisions(preview, decisions or {})

    def archive_nonflights(self, preview: FlightLogPreview, root: Path,
                           selected: Sequence[Path]) -> tuple[dict[Path, Path], list[str]]:
        self._check_owner(preview)
        root = root.resolve()
        archive = root / NONFLIGHT_ARCHIVE
        eligible = set(preview.archivable_sources(root))
        if not set(selected) <= eligible:
            raise ValueError("The selection contains a source outside this non-flight review.")
        moved: dict[Path, Path] = {}
        errors: list[str] = []
        for source in dict.fromkeys(selected):
            relative = source.relative_to(root)
            target = archive / relative
            output = preview._archive_files[source]
            try:
                if source.resolve() != source or target.resolve() != target:
                    raise ValueError("a linked path changed the source or archive location")
                if _fingerprint(output) != preview._output_fingerprints[output]:
                    raise ValueError("the maintained flight log changed since review")
                expected = preview._source_fingerprints[source]
                if expected is None:
                    raise ValueError("missing source fingerprint")
                target.parent.mkdir(parents=True, exist_ok=True)
                # Exclusive creation also handles races with another archiver. Keep the
                # original until the closed archive copy and the source both verify.
                before = source.stat()
                saved = target.open("xb")
                try:
                    with saved, source.open("rb") as original:
                        shutil.copyfileobj(original, saved, 1024 * 1024)
                        saved.flush()
                        os.fsync(saved.fileno())
                    shutil.copystat(source, target)
                    if (_fingerprint(target) != expected or not self._same_source(before, source.stat())
                            or _fingerprint(source) != expected):
                        raise ValueError("source changed since review or archive verification failed")
                    if _fingerprint(output) != preview._output_fingerprints[output]:
                        raise ValueError("the maintained flight log changed during archiving")
                    source.unlink()
                except (OSError, ValueError):
                    if source.exists():
                        target.unlink()
                    raise
                moved[source] = target
            except (OSError, ValueError) as exc:
                errors.append(f"{relative}: {exc}")
        return moved, errors

    def with_decisions(self, preview: FlightLogPreview, decisions: Mapping[str, str]) -> FlightLogPreview:
        self._check_owner(preview)
        if set(decisions) - set(preview._flights):
            raise ValueError("Decisions contain stale or unknown candidate IDs; preview again")
        if any(value not in ("accept", "reject") for value in decisions.values()):
            raise ValueError("Candidate decisions must be accept or reject")
        result = replace(preview, report=copy.deepcopy(preview.report), _plans={})
        accepted: dict[str, list[engine.Flight]] = defaultdict(list)
        pending: set[str] = set()
        accepted_count = rejected_count = 0
        for candidate in result.candidates:
            candidate_id = candidate["candidate_id"]
            default = "accept" if candidate["confidence"] == "confirmed" else "pending"
            decision = decisions.get(candidate_id, default)
            candidate["decision"] = decision
            if decision == "pending":
                pending.add(candidate_id)
            elif decision == "accept":
                accepted_count += 1
                accepted[candidate["plane"]].append(preview._flights[candidate_id])
            else:
                rejected_count += 1
        updates = []
        errors = [{"path": file["path"], "aircraft": file["plane"], "error": file["detail"]}
                  for file in result.report["files"] if file["status"] == "error"]
        incomplete_planes = {error["aircraft"] for error in errors}
        for aircraft, flights in sorted(accepted.items(), key=lambda item: item[0].casefold()):
            path = preview._output_paths[aircraft]
            try:
                if result._rebuild and aircraft in incomplete_planes:
                    raise ValueError("Cannot rebuild an aircraft with telemetry read errors; fix the sources and preview again")
                column_order = next((order for name, order in preview._settings.get("column_orders", {}).items()
                                     if name.casefold() == aircraft.casefold()), [])
                update, text = self._plan(path, aircraft, flights, preview._output_texts[path], result._rebuild, column_order)
                updates.append(update)
                result._plans[path] = text
            except (OSError, ValueError) as exc:
                update = {"plane": aircraft, "aircraft": aircraft, "path": str(path), "error": str(exc),
                          "added": 0, "duplicates": 0, "late": [], "removed": [], "unmatched_notes": []}
                updates.append(update)
                errors.append({"path": str(path), "aircraft": aircraft, "error": str(exc)})
        result.report.update(
            updates=updates, errors=errors, writes_blocked=bool(pending), pending_candidates=sorted(pending),
            candidates_accepted=accepted_count, candidates_rejected=rejected_count, candidates_pending=len(pending),
            files_with_flights=sum(file["status"] == "flight" for file in result.report["files"]),
            flights_detected=len(result.candidates), uncertain_flights=sum(c["confidence"] == "uncertain" for c in result.candidates),
            rejected_candidates=sum(len(file["rejected_candidates"]) for file in result.report["files"]),
            read_errors=sum(file["status"] == "error" for file in result.report["files"]),
            rows_added=sum(update["added"] for update in updates),
            duplicates=sum(update["duplicates"] for update in updates),
            late_conflicts=sum(len(update["late"]) for update in updates),
        )
        return result

    def _plan(self, path: Path, aircraft: str, flights: Sequence[engine.Flight],
              existing_text: str | None, rebuild: bool,
              column_order: Sequence[str] = ()) -> tuple[dict[str, Any], str]:
        ordered = sorted(flights, key=lambda flight: (flight.start, flight.end, str(flight.source).casefold()))
        distinct: dict[tuple[datetime, str], engine.Flight] = {}
        for flight in ordered:
            distinct.setdefault(_flight_key(flight), flight)
        source_duplicates = len(ordered) - len(distinct)
        ordered = list(distinct.values())
        # Parse the reviewed snapshot, never silently adopt changed live output.
        old_rows: list[engine.ExistingRow] = []
        before: list[str] = []
        insertion_offset = 0
        schema = engine.TableSchema((), ())
        if existing_text is not None:
            old_rows, insertion_offset, schema = _parse_snapshot(existing_text, path.name)
            before, _ = _table_cells(existing_text)
        old_keys = {(row.timestamp, row.duration) for row in old_rows}
        update: dict[str, Any] = {"plane": aircraft, "aircraft": aircraft, "path": str(path),
                                  "created": existing_text is None, "rebuilt": rebuild, "added": 0,
                                  "duplicates": source_duplicates, "late": [], "removed": [], "unmatched_notes": []}
        if rebuild:
            notes = {(row.timestamp, row.duration): row.notes for row in old_rows}
            ordered = [replace(flight, notes=_preserved_notes(notes.get(_flight_key(flight), ""), flight.notes))
                       for flight in ordered]
            new_keys = {_flight_key(flight) for flight in ordered}
            update["removed"] = [{"datetime": row.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                                   "duration": row.duration, "notes": row.notes}
                                  for row in old_rows if (row.timestamp, row.duration) not in new_keys]
            update["unmatched_notes"] = [row for row in update["removed"] if row["notes"]]
            update["added"] = len(new_keys - old_keys)
            update["retained"] = len(new_keys & old_keys)
            text = engine.new_log_text(aircraft, ordered)
            if existing_text is not None:
                # Only replace the generated table. Keep pilot text outside it.
                old_lines = existing_text.splitlines(keepends=True)
                table_index = next(i for i, line in enumerate(old_lines) if engine.schema_from_header(line) is not None)
                table_start = sum(len(line) for line in old_lines[:table_index])
                table = "\n".join(text.splitlines()[2:]) + "\n"
                if "\r\n" in existing_text:
                    table = table.replace("\n", "\r\n")
                text = existing_text[:table_start] + table + existing_text[insertion_offset:]
        elif existing_text is None:
            update["added"] = len(ordered)
            text = engine.new_log_text(aircraft, ordered)
        else:
            missing_max = [sensor for sensor in engine.maximum_columns(ordered)
                           if engine.normalize_header(sensor) not in
                           {engine.normalize_header(value) for value in schema.maximum_sensors}]
            missing_vfr = [engine.VFR_LABELS[family] for family in engine.vfr_columns(ordered)
                           if family not in schema.vfr_families]
            if missing_max or missing_vfr:
                raise ValueError("New metric columns require a reviewed rebuild: " + ", ".join(missing_max + missing_vfr))
            additions = []
            last_timestamp = old_rows[-1].timestamp if old_rows else None
            for flight in ordered:
                key = _flight_key(flight)
                if key in old_keys:
                    update["duplicates"] += 1
                elif last_timestamp is not None and key[0] < last_timestamp:
                    update["late"].append({"datetime": flight.display_datetime, "source": str(flight.source)})
                else:
                    additions.append(flight)
                    old_keys.add(key)
                    last_timestamp = key[0]
            update["added"] = len(additions)
            newline = "\r\n" if "\r\n" in existing_text else "\n"
            next_number = max((row.number for row in old_rows), default=0) + 1
            inserted = "".join(engine.flight_row(next_number + i, flight, schema.maximum_sensors,
                                                schema.vfr_families, schema.headers) + newline
                               for i, flight in enumerate(additions))
            if additions and insertion_offset and not existing_text[:insertion_offset].endswith(("\n", "\r")):
                inserted = newline + inserted
            text = existing_text[:insertion_offset] + inserted + existing_text[insertion_offset:]
        if rebuild or existing_text is None:
            headers, _ = _table_cells(text)
            canonical_before = engine.split_markdown_row(engine.table_header(schema.maximum_sensors, schema.vfr_families))
            preferred = column_order or (before if before != canonical_before else [])
            ordered_headers = [header for header in preferred if header in headers]
            ordered_headers.extend(header for header in headers if header not in ordered_headers)
            text = _reorder_table_text(text, ordered_headers)
        headers, rows = _table_cells(text)
        update.update(headers=headers, rows=rows, schema_changes=_schema_changes(before, headers))
        return update, text

    def generate(self, preview: FlightLogPreview, approve_rebuild: bool = False) -> dict[str, Any]:
        self._check_owner(preview)
        if preview.pending_candidates:
            raise ValueError("No logs were written: explicitly accept or reject every uncertain candidate first")
        if preview._rebuild and not approve_rebuild:
            raise ValueError("No logs were written: rebuilding requires explicit approval of schema changes, removals and unmatched notes")
        for source, expected in preview._source_fingerprints.items():
            if _fingerprint(source) != expected:
                raise ValueError(f"No logs were written: telemetry changed since preview ({source.name}); preview again")
        for output, expected in preview._output_fingerprints.items():
            if _fingerprint(output) != expected:
                raise ValueError(f"No logs were written: local log changed since preview ({output.name}); preview again")
        report = copy.deepcopy(preview.report)
        report.update(dry_run=False, writes_blocked=False, changed_logs=[], rows_added=0)
        for update in report["updates"]:
            path = Path(update["path"])
            if path not in preview._plans:
                continue
            try:
                text = preview._plans[path]
                if text != preview._output_texts[path]:
                    self._atomic_write(path, text, expected_fingerprint=preview._output_fingerprints[path])
                    report["changed_logs"].append(str(path))
                report["rows_added"] += update["added"]
                update["written"] = True
            except (OSError, ValueError) as exc:
                update["error"] = str(exc)
                update["written"] = False
                report["errors"].append({"path": str(path), "aircraft": update["plane"], "error": str(exc)})
        return report

    def _check_owner(self, preview: FlightLogPreview) -> None:
        if preview._service_root != self.output_dir:
            raise ValueError("The preview belongs to a different output directory")

    def read_log(self, aircraft: str) -> FlightLogDocument:
        path = self._path_for(aircraft)
        fingerprint = _fingerprint(path)
        if fingerprint is None:
            raise FileNotFoundError(f"No flight log exists for {aircraft}")
        with path.open(encoding="utf-8", newline="") as handle:
            text = handle.read()
        _parse_snapshot(text, path.name)
        if fingerprint != _fingerprint(path):
            raise ValueError("Flight log changed while reading; reload it")
        headers, rows = _table_cells(text)
        return FlightLogDocument(aircraft, path, headers, rows, text, fingerprint)

    def save_notes(self, aircraft: str, row_number: int, notes: str,
                   expected_fingerprint: str | None = None) -> FlightLogDocument:
        document = self.read_log(aircraft)
        if expected_fingerprint is not None and document.fingerprint != expected_fingerprint:
            raise ValueError("Flight log changed since it was opened; reload before saving Notes")
        lines = document.text.splitlines(keepends=True)
        header_index = next(index for index, line in enumerate(lines) if engine.schema_from_header(line) is not None)
        found = False
        for index in range(header_index + 2, len(lines)):
            if not lines[index].strip().startswith("|"):
                break
            cells = engine.split_markdown_row(lines[index])
            if int(cells[document.headers.index("Flight number")]) != row_number:
                continue
            cells[document.headers.index("Notes")] = notes
            ending = "\r\n" if lines[index].endswith("\r\n") else "\n" if lines[index].endswith("\n") else ""
            lines[index] = "| " + " | ".join(engine.escape_cell(cell) for cell in cells) + " |" + ending
            found = True
            break
        if not found:
            raise ValueError(f"Flight number {row_number} does not exist in {aircraft}")
        self._atomic_write(document.path, "".join(lines), expected_fingerprint=document.fingerprint)
        return self.read_log(aircraft)

    def save_notes_many(self, updates: Mapping[str, Mapping[int, str]],
                        expected_fingerprints: Mapping[str, str]) -> dict[str, Any]:
        """Apply reviewed notes after validating every aircraft and flight row.

        Fingerprints are required for every affected aircraft. No local file is
        changed if any row is unknown or any reviewed document has changed.
        A filesystem write failure is reported per aircraft after validation.
        """
        fingerprints = {name.casefold(): value for name, value in expected_fingerprints.items()}
        documents: dict[str, FlightLogDocument] = {}
        plans: dict[str, str] = {}
        seen: set[str] = set()
        for aircraft, note_updates in updates.items():
            if aircraft.casefold() in seen:
                raise ValueError(f"Duplicate note updates for aircraft {aircraft}")
            seen.add(aircraft.casefold())
            document = self.read_log(aircraft)
            if fingerprints.get(aircraft.casefold()) != document.fingerprint:
                raise ValueError(f"No Notes were saved: {aircraft} changed since review; reload before importing")
            known = {int(row[document.headers.index("Flight number")]) for row in document.rows}
            if set(note_updates) - known:
                raise ValueError(f"No Notes were saved: unknown flight numbers for {aircraft}")
            if any(not isinstance(number, int) or isinstance(number, bool) or not isinstance(notes, str)
                   for number, notes in note_updates.items()):
                raise ValueError("Note updates require integer flight numbers and text")
            lines = document.text.splitlines(keepends=True)
            header_index = next(i for i, line in enumerate(lines) if engine.schema_from_header(line) is not None)
            for index in range(header_index + 2, len(lines)):
                if not lines[index].strip().startswith("|"):
                    break
                cells = engine.split_markdown_row(lines[index])
                number = int(cells[document.headers.index("Flight number")])
                if number not in note_updates:
                    continue
                cells[document.headers.index("Notes")] = note_updates[number]
                ending = "\r\n" if lines[index].endswith("\r\n") else "\n" if lines[index].endswith("\n") else ""
                lines[index] = "| " + " | ".join(engine.escape_cell(cell) for cell in cells) + " |" + ending
            documents[aircraft] = document
            plans[aircraft] = "".join(lines)
        for document in documents.values():
            if _fingerprint(document.path) != document.fingerprint:
                raise ValueError(f"No Notes were saved: {document.aircraft} changed during review; reload before importing")
        report: dict[str, Any] = {"rows_updated": 0, "changed_logs": [], "errors": []}
        for aircraft, text in plans.items():
            document = documents[aircraft]
            try:
                if text != document.text:
                    self._atomic_write(document.path, text, expected_fingerprint=document.fingerprint)
                    report["changed_logs"].append(str(document.path))
                    report["rows_updated"] += len(updates[aircraft])
            except (OSError, ValueError) as exc:
                report["errors"].append({"aircraft": aircraft, "path": str(document.path), "error": str(exc)})
        return report

    def reorder_columns(self, aircraft: str, headers: Sequence[str],
                        expected_fingerprint: str) -> FlightLogDocument:
        """Reorder existing cells without regenerating any flight or pilot note."""
        document = self.read_log(aircraft)
        if document.fingerprint != expected_fingerprint:
            raise ValueError("Flight log changed since it was opened; reload before saving column order")
        text = _reorder_table_text(document.text, headers)
        settings = self.load_settings()
        orders = settings.setdefault("column_orders", {})
        for name in list(orders):
            if name.casefold() == aircraft.casefold():
                del orders[name]
        orders[aircraft] = list(headers)
        if text != document.text:
            self._atomic_write(document.path, text, expected_fingerprint=document.fingerprint)
        self.save_settings(settings)
        return self.read_log(aircraft)

    def with_column_order(self, preview: FlightLogPreview, aircraft: str,
                          headers: Sequence[str]) -> FlightLogPreview:
        """Adjust a new/rebuilt table's reviewed layout without another scan."""
        self._check_owner(preview)
        update = next(item for item in preview.updates if item["aircraft"] == aircraft)
        if not update.get("created") and not preview.rebuild:
            raise ValueError("Reorder an existing table in Flight logs before appending")
        if len(headers) != len(update["headers"]) or set(headers) != set(update["headers"]):
            raise ValueError("Column order must include every current column exactly once")
        settings = copy.deepcopy(preview._settings)
        orders = settings.setdefault("column_orders", {})
        for name in list(orders):
            if name.casefold() == aircraft.casefold():
                del orders[name]
        orders[aircraft] = list(headers)
        result = replace(preview, _settings=settings)
        decisions = {candidate["candidate_id"]: candidate["decision"] for candidate in preview.candidates
                     if candidate["decision"] != "pending"}
        return self.with_decisions(result, decisions)

    def export_workbook_rows(self, selected_aircraft: Sequence[str] | None = None) -> dict[str, list[list[str]]]:
        selected = None if selected_aircraft is None else {name.casefold() for name in selected_aircraft}
        result = {}
        for aircraft in self.list_aircraft():
            if selected is not None and aircraft.casefold() not in selected:
                continue
            document = self.read_log(aircraft)
            result[aircraft] = [document.headers, *document.rows]
        return result

    @staticmethod
    def _atomic_write(path: Path, text: str, *, expected_fingerprint: str | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent,
                                             prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
                temporary = handle.name
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            if _fingerprint(path) != expected_fingerprint:
                raise ValueError(f"{path.name} changed before saving; reload or preview again")
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)


def _parse_snapshot(text: str, filename: str) -> tuple[list[engine.ExistingRow], int, engine.TableSchema]:
    """Validate reviewed text without a temporary file or a second live read."""
    lines = text.splitlines(keepends=True)
    index = next((index for index, line in enumerate(lines) if engine.schema_from_header(line) is not None), None)
    if index is None or index + 1 >= len(lines):
        raise ValueError(f"{filename} does not contain the expected flight-log table header")
    schema = engine.schema_from_header(lines[index])
    assert schema is not None
    expected_cells = 4 + len(schema.vfr_families) + len(schema.maximum_sensors)
    separator = engine.split_markdown_row(lines[index + 1])
    if len(separator) != expected_cells or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator):
        raise ValueError(f"{filename} has an invalid table separator")
    rows: list[engine.ExistingRow] = []
    insertion_line = index + 2
    for row_index in range(insertion_line, len(lines)):
        if not lines[row_index].strip().startswith("|"):
            break
        cells = engine.split_markdown_row(lines[row_index])
        if len(cells) != expected_cells:
            raise ValueError(f"Cannot safely parse row {row_index + 1} in {filename}: wrong cell count")
        try:
            number = int(cells[schema.headers.index("Flight number")])
            timestamp = datetime.strptime(cells[schema.headers.index("Date/time")], "%Y-%m-%d %H:%M:%S")
            duration = cells[schema.headers.index("Flight duration")]
            if not re.fullmatch(r"\d{2,}:[0-5]\d:[0-5]\d", duration):
                raise ValueError("invalid flight duration")
        except ValueError as exc:
            raise ValueError(f"Cannot safely parse row {row_index + 1} in {filename}: {exc}") from exc
        rows.append(engine.ExistingRow(number, timestamp, duration, cells[schema.headers.index("Notes")]))
        insertion_line = row_index + 1
    if any(rows[i].timestamp > rows[i + 1].timestamp or rows[i].number >= rows[i + 1].number
           for i in range(len(rows) - 1)) or any(row.number < 1 for row in rows):
        raise ValueError(f"{filename} must have ascending flight dates and numbers")
    return rows, sum(len(line) for line in lines[:insertion_line]), schema


def _reorder_table_text(text: str, headers: Sequence[str]) -> str:
    current, _ = _table_cells(text)
    if len(headers) != len(current) or set(headers) != set(current):
        raise ValueError("Column order must include every current column exactly once")
    if list(headers) == current:
        return text
    permutation = [current.index(header) for header in headers]
    lines = text.splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if engine.schema_from_header(line) is not None)
    for index in range(start, len(lines)):
        line = lines[index]
        if not line.strip().startswith("|"):
            break
        # Permute the original cell tokens, retaining escaped pipes, whitespace,
        # alignments, Notes and line endings rather than reformatting history.
        protected = line.replace("\\|", "\0PIPE\0")
        left, right = protected.index("|"), protected.rfind("|")
        cells = protected[left + 1:right].split("|")
        if len(cells) != len(current):
            raise ValueError("Cannot safely reorder a table with inconsistent cell counts")
        lines[index] = (protected[:left + 1] + "|".join(cells[column] for column in permutation)
                        + protected[right:]).replace("\0PIPE\0", "\\|")
    return "".join(lines)


def _preserved_notes(old_notes: str, generated_notes: str) -> str:
    if not old_notes:
        return generated_notes
    present = {part.strip() for part in old_notes.split(";")}
    additions = [part.strip() for part in generated_notes.split(";") if part.strip() and part.strip() not in present]
    return old_notes + ("; " + "; ".join(additions) if additions else "")
