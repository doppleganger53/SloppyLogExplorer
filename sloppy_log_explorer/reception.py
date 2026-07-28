"""Flying-site indexing and multi-log reception heatmap aggregation."""

from __future__ import annotations

import csv
import math
import os
import re
import statistics
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .models import (
    DetectedSiteCluster,
    GpsColumns,
    LibraryLogInfo,
    RECEPTION_INDEX_VERSION,
    ReceptionCell,
    ReceptionLogRecord,
    ReceptionScanResult,
    TelemetryChannelCoverage,
)
from .parser import (
    InvalidTelemetryLogError,
    _clean_columns,
    _coerce_numeric_series,
    _deduplicate_columns,
    _detect_time,
    _has_date_header,
    _has_position_header_hint,
    _numeric_columns,
    _validate_table_structure,
    detect_gps_columns,
    load_log,
    relative_seconds,
)
from .plotting import (
    GpsCandidate,
    GpsPoint,
    _coerce_float,
    _drop_isolated_gps_outliers,
    _is_origin_placeholder,
)


SAMPLE_STRIDE = 20
# Position-hinted logs bypass this limit and receive one whole-file sparse pass.
# This bounded prefix only protects support for unknown, unhinted GPS formats.
GPS_PROBE_RECORDS = 200
SITE_TOLERANCE_KM = 2.0
DEFAULT_CELL_SIZE_METERS = 5.0
HEATMAP_SITE_RADIUS_KM = 10.0
EARTH_RADIUS_METERS = 6_371_008.8
_CALENDAR_LITERAL_RE = re.compile(
    r"(?:\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b|\b\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}\b)"
)

ProgressCallback = Callable[[int, int, str], None]
CancellationCheck = Callable[[], bool]


def _canonical_path(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).expanduser().resolve(strict=False)))


def _sampled_csv(
    path: Path,
    sample_stride: int,
    *,
    max_records: int | None = None,
    full_scan_if_position_hint: bool = False,
) -> pd.DataFrame:
    """Read each Nth row, unless a position-hinted header overrides the limit."""
    stride = max(1, int(sample_stride))
    requested_record_limit = max(1, int(max_records)) if max_records is not None else None
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        sniff_sample = handle.read(4096)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sniff_sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(handle, dialect)
        try:
            header = next(reader)
        except StopIteration:
            return pd.DataFrame()
        columns = _deduplicate_columns(header)
        record_limit = (
            None
            if full_scan_if_position_hint and _has_position_header_hint(columns)
            else requested_record_limit
        )
        width = len(header)
        rows: list[list[str]] = []
        short_log_rows: list[list[str]] = []
        data_index = 0
        for row in reader:
            if not row:
                continue
            if record_limit is not None and data_index >= record_limit:
                break
            if len(row) < width:
                row = row + [""] * (width - len(row))
            elif len(row) > width:
                row = row[:width]
            if data_index < stride:
                # Logs shorter than one stride are cheap to retain in full and
                # need at least two rows for the shared GPS detector to prove a
                # coordinate pair is telemetry rather than an incidental value.
                short_log_rows.append(row)
            if data_index % stride == 0:
                rows.append(row)
            data_index += 1
        if data_index <= stride:
            rows = short_log_rows
    return pd.DataFrame(rows, columns=pd.Index(columns))


def _inspect_index_sample(
    dataframe: pd.DataFrame,
    file_path: Path,
    modified: float,
) -> tuple[
    pd.DataFrame,
    pd.Series | None,
    frozenset[str],
    GpsColumns | None,
    tuple[str, ...],
    date,
    bool,
]:
    dataframe = _clean_columns(dataframe)
    _validate_table_structure(dataframe, file_path)
    parsed_time, timeline_columns = _detect_time(dataframe)
    numeric_columns = _numeric_columns(dataframe, timeline_columns)
    dataframe = dataframe.copy()
    gps = detect_gps_columns(dataframe, numeric_columns)
    numeric_columns = _numeric_columns(dataframe, timeline_columns)
    if not numeric_columns:
        raise InvalidTelemetryLogError(
            f"{file_path.name} has no usable numeric telemetry columns."
        )
    gps_position_columns = {gps.latitude, gps.longitude} if gps is not None else set()
    channels = tuple(
        column
        for column in numeric_columns
        if column not in timeline_columns
        and column not in gps_position_columns
        and not str(column).startswith("__")
    )
    flight_date, date_inferred = _trustworthy_flight_date(
        dataframe, parsed_time, timeline_columns, modified
    )
    return (
        dataframe,
        parsed_time,
        timeline_columns,
        gps,
        channels,
        flight_date,
        date_inferred,
    )


def _trustworthy_flight_date(
    dataframe: pd.DataFrame,
    parsed_time: pd.Series | None,
    timeline_columns: frozenset[str],
    modified: float,
) -> tuple[date, bool]:
    if parsed_time is not None and parsed_time.notna().any():
        date_columns = [column for column in timeline_columns if _has_date_header(str(column))]
        has_calendar_text = any(
            _CALENDAR_LITERAL_RE.search(str(value).strip()) is not None
            for column in date_columns
            if column in dataframe.columns
            for value in dataframe[column].dropna()
        )
        if has_calendar_text:
            timestamp = pd.Timestamp(parsed_time.dropna().iloc[0])
            if 1980 <= timestamp.year <= datetime.now().year + 1:
                return timestamp.date(), False
    return datetime.fromtimestamp(modified).date(), True


def _valid_gps_candidates(
    dataframe: pd.DataFrame,
    latitude_column: str,
    longitude_column: str,
    *,
    time_values: Sequence[object] | None = None,
    elapsed_values: Sequence[float] | None = None,
    value_column: str | None = None,
) -> list[GpsCandidate]:
    latitude = _coerce_numeric_series(dataframe[latitude_column])
    longitude = _coerce_numeric_series(dataframe[longitude_column])
    values = _coerce_numeric_series(dataframe[value_column]) if value_column else None
    candidates: list[GpsCandidate] = []
    for index in range(len(dataframe)):
        lat = _coerce_float(latitude.iloc[index])
        lon = _coerce_float(longitude.iloc[index])
        if lat is None or lon is None or not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            continue
        if _is_origin_placeholder(lat, lon):
            continue
        value = _coerce_float(values.iloc[index]) if values is not None else None
        point: GpsPoint = {
            "lat": lat,
            "lon": lon,
            "alt": 0.0,
            "row": index + 1,
            "elapsedSeconds": (
                float(elapsed_values[index]) if elapsed_values is not None and index < len(elapsed_values) else float(index)
            ),
            "value": value,
        }
        point_time: object = time_values[index] if time_values is not None and index < len(time_values) else None
        candidates.append((point, point_time))
    return _drop_isolated_gps_outliers(candidates)


def _spherical_centroid(points: Iterable[tuple[float, float]]) -> tuple[float, float]:
    vectors: list[tuple[float, float, float]] = []
    for latitude, longitude in points:
        lat = math.radians(latitude)
        lon = math.radians(longitude)
        vectors.append((math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat)))
    if not vectors:
        raise ValueError("cannot calculate a flying-site center without GPS points")
    x = sum(value[0] for value in vectors) / len(vectors)
    y = sum(value[1] for value in vectors) / len(vectors)
    z = sum(value[2] for value in vectors) / len(vectors)
    longitude = math.degrees(math.atan2(y, x))
    hypotenuse = math.hypot(x, y)
    latitude = math.degrees(math.atan2(z, hypotenuse))
    return latitude, longitude


def _haversine_km(left_lat: float, left_lon: float, right_lat: float, right_lon: float) -> float:
    left_phi = math.radians(left_lat)
    right_phi = math.radians(right_lat)
    delta_phi = right_phi - left_phi
    delta_lambda = math.radians(right_lon - left_lon)
    value = math.sin(delta_phi / 2.0) ** 2 + math.cos(left_phi) * math.cos(right_phi) * math.sin(delta_lambda / 2.0) ** 2
    return (2.0 * EARTH_RADIUS_METERS * math.asin(min(1.0, math.sqrt(value)))) / 1000.0


def _site_local_gps_candidates(
    candidates: Sequence[GpsCandidate],
    radius_km: float = HEATMAP_SITE_RADIUS_KM,
) -> list[GpsCandidate]:
    """Keep the dominant local fix cloud used to identify a flying site."""
    if len(candidates) < 2:
        return []

    localized = list(candidates)
    fully_resolved = [
        candidate
        for candidate in localized
        if abs(float(candidate[0]["lat"])) > 1e-9
        and abs(float(candidate[0]["lon"])) > 1e-9
    ]
    if len(fully_resolved) >= 2:
        # A receiver awaiting lock commonly reports one axis as exactly zero
        # while retaining the other. Preserve true equator/prime-meridian logs
        # when no fully resolved alternative exists.
        localized = fully_resolved

    reference_longitude = float(localized[0][0]["lon"])
    unwrapped_longitudes = [
        reference_longitude
        + ((float(candidate[0]["lon"]) - reference_longitude + 180.0) % 360.0)
        - 180.0
        for candidate in localized
    ]
    seed_latitude = float(statistics.median(float(candidate[0]["lat"]) for candidate in localized))
    seed_longitude = ((float(statistics.median(unwrapped_longitudes)) + 180.0) % 360.0) - 180.0
    inliers = [
        candidate
        for candidate in localized
        if _haversine_km(
            seed_latitude,
            seed_longitude,
            float(candidate[0]["lat"]),
            float(candidate[0]["lon"]),
        )
        <= radius_km + 1e-9
    ]
    if len(inliers) < 2 or len(inliers) * 2 <= len(localized):
        return []

    center_latitude, center_longitude = _spherical_centroid(
        (float(candidate[0]["lat"]), float(candidate[0]["lon"]))
        for candidate in inliers
    )
    refined = [
        candidate
        for candidate in localized
        if _haversine_km(
            center_latitude,
            center_longitude,
            float(candidate[0]["lat"]),
            float(candidate[0]["lon"]),
        )
        <= radius_km + 1e-9
    ]
    return refined if len(refined) >= 2 and len(refined) * 2 > len(localized) else []


def _file_metadata(path: Path) -> tuple[int, int, float]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_mtime


def index_log(
    path: str | Path,
    library_root: str | Path,
    sample_stride: int = SAMPLE_STRIDE,
    gps_probe_records: int = GPS_PROBE_RECORDS,
) -> ReceptionLogRecord:
    """Probe for GPS, then build one cached record from a whole-file row sample."""
    file_path = Path(path).resolve(strict=False)
    root = Path(library_root).resolve(strict=False)
    try:
        file_size, modified_ns, modified = _file_metadata(file_path)
    except OSError as exc:
        return ReceptionLogRecord(
            root,
            file_path,
            0,
            0,
            "io_error",
            datetime.now().date(),
            True,
            None,
            None,
            (),
            f"{type(exc).__name__}: {exc}",
        )

    try:
        probe = _sampled_csv(
            file_path,
            sample_stride,
            max_records=gps_probe_records,
            full_scan_if_position_hint=True,
        )
        probe_is_full_sample = _has_position_header_hint(probe.columns)
        (
            probe,
            probe_time,
            _probe_timeline_columns,
            probe_gps,
            probe_channels,
            probe_flight_date,
            probe_date_inferred,
        ) = _inspect_index_sample(probe, file_path, modified)
        probe_candidates = (
            _valid_gps_candidates(
                probe,
                probe_gps.latitude,
                probe_gps.longitude,
                time_values=list(probe_time) if probe_time is not None else None,
            )
            if probe_gps is not None
            else []
        )
        if not probe_candidates:
            return ReceptionLogRecord(
                root,
                file_path,
                file_size,
                modified_ns,
                "no_gps",
                probe_flight_date,
                probe_date_inferred,
                None,
                None,
                probe_channels,
            )

        if probe_is_full_sample:
            dataframe = probe
            parsed_time = probe_time
            gps = probe_gps
            sampled_channels = probe_channels
            sampled_flight_date = probe_flight_date
            sampled_date_inferred = probe_date_inferred
        else:
            (
                dataframe,
                parsed_time,
                _timeline_columns,
                gps,
                sampled_channels,
                sampled_flight_date,
                sampled_date_inferred,
            ) = _inspect_index_sample(
                _sampled_csv(file_path, sample_stride),
                file_path,
                modified,
            )
        channels = tuple(dict.fromkeys((*probe_channels, *sampled_channels)))
        flight_date = probe_flight_date if not probe_date_inferred else sampled_flight_date
        date_inferred = probe_date_inferred and sampled_date_inferred
        if gps is None:
            return ReceptionLogRecord(
                root,
                file_path,
                file_size,
                modified_ns,
                "no_gps",
                flight_date,
                date_inferred,
                None,
                None,
                channels,
            )
        time_values = list(parsed_time) if parsed_time is not None else None
        candidates = _site_local_gps_candidates(
            _valid_gps_candidates(
                dataframe,
                gps.latitude,
                gps.longitude,
                time_values=time_values,
            )
        )
        if not candidates:
            return ReceptionLogRecord(
                root,
                file_path,
                file_size,
                modified_ns,
                "no_gps",
                flight_date,
                date_inferred,
                None,
                None,
                channels,
            )
        center_latitude, center_longitude = _spherical_centroid(
            (float(point["lat"]), float(point["lon"])) for point, _ in candidates
        )
        return ReceptionLogRecord(
            root,
            file_path,
            file_size,
            modified_ns,
            "ok",
            flight_date,
            date_inferred,
            center_latitude,
            center_longitude,
            channels,
        )
    except OSError as exc:
        status = "io_error"
        message = f"{type(exc).__name__}: {exc}"
    except (InvalidTelemetryLogError, ValueError, TypeError, pd.errors.ParserError) as exc:
        status = "malformed"
        message = f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        status = "malformed"
        message = f"{type(exc).__name__}: {exc}"
    return ReceptionLogRecord(
        root,
        file_path,
        file_size,
        modified_ns,
        status,
        datetime.fromtimestamp(modified).date(),
        True,
        None,
        None,
        (),
        message,
    )


def _coerce_record(value: ReceptionLogRecord | Mapping[str, Any]) -> ReceptionLogRecord:
    if isinstance(value, ReceptionLogRecord):
        return value
    raw_date = value.get("flight_date")
    if isinstance(raw_date, date):
        flight_date = raw_date
    elif raw_date:
        flight_date = date.fromisoformat(str(raw_date)[:10])
    else:
        modified_ns = int(value.get("mtime_ns") or value.get("modified_ns") or 0)
        flight_date = datetime.fromtimestamp(modified_ns / 1_000_000_000).date() if modified_ns else datetime.now().date()
    root = Path(str(value.get("library_root") or Path(str(value.get("file_path") or value.get("path"))).parent))
    raw_latitude = value.get("center_latitude")
    if raw_latitude is None:
        raw_latitude = value.get("centroid_latitude")
    raw_longitude = value.get("center_longitude")
    if raw_longitude is None:
        raw_longitude = value.get("centroid_longitude")
    return ReceptionLogRecord(
        root,
        Path(str(value.get("file_path") or value.get("path"))),
        int(value.get("file_size") or value.get("size") or 0),
        int(value.get("mtime_ns") or value.get("modified_ns") or 0),
        str(value.get("status") or "malformed"),
        flight_date,
        bool(value.get("date_inferred") or value.get("date_is_inferred")),
        _coerce_float(raw_latitude),
        _coerce_float(raw_longitude),
        tuple(str(channel) for channel in (value.get("channels") or value.get("numeric_channels") or ())),
        str(value.get("error") or value.get("error_message") or ""),
        int(value["site_id"]) if value.get("site_id") is not None else None,
        int(value.get("index_version") or 0),
    )


def _cluster_center(records: Sequence[ReceptionLogRecord]) -> tuple[float, float]:
    return _spherical_centroid(
        (float(record.center_latitude), float(record.center_longitude))
        for record in records
        if record.center_latitude is not None and record.center_longitude is not None
    )


def _cluster_is_valid(records: Sequence[ReceptionLogRecord], tolerance_km: float) -> tuple[bool, float, float]:
    center_latitude, center_longitude = _cluster_center(records)
    valid = all(
        record.center_latitude is not None
        and record.center_longitude is not None
        and _haversine_km(
            center_latitude,
            center_longitude,
            record.center_latitude,
            record.center_longitude,
        )
        <= tolerance_km + 1e-9
        for record in records
    )
    return valid, center_latitude, center_longitude


def cluster_reception_records(
    records: Iterable[ReceptionLogRecord | Mapping[str, Any]],
    tolerance_km: float = SITE_TOLERANCE_KM,
) -> list[DetectedSiteCluster]:
    """Group log centroids while keeping every member within the site radius."""
    tolerance = max(0.0, float(tolerance_km))
    normalized = [_coerce_record(record) for record in records]
    candidates = sorted(
        (
            record
            for record in normalized
            if record.status == "ok"
            and record.center_latitude is not None
            and record.center_longitude is not None
        ),
        key=lambda record: _canonical_path(record.file_path),
    )
    groups: list[list[ReceptionLogRecord]] = []
    for record in candidates:
        compatible: list[tuple[float, int]] = []
        for index, group in enumerate(groups):
            valid, latitude, longitude = _cluster_is_valid([*group, record], tolerance)
            if valid:
                record_latitude = record.center_latitude
                record_longitude = record.center_longitude
                if record_latitude is None or record_longitude is None:
                    continue
                compatible.append(
                    (_haversine_km(latitude, longitude, record_latitude, record_longitude), index)
                )
        if compatible:
            groups[min(compatible)[1]].append(record)
        else:
            groups.append([record])

    while True:
        merge_candidates: list[tuple[float, int, int]] = []
        for left in range(len(groups)):
            left_center = _cluster_center(groups[left])
            for right in range(left + 1, len(groups)):
                valid, _, _ = _cluster_is_valid([*groups[left], *groups[right]], tolerance)
                if valid:
                    right_center = _cluster_center(groups[right])
                    merge_candidates.append(
                        (_haversine_km(*left_center, *right_center), left, right)
                    )
        if not merge_candidates:
            break
        _, left, right = min(merge_candidates)
        groups[left] = sorted(
            [*groups[left], *groups[right]], key=lambda record: _canonical_path(record.file_path)
        )
        del groups[right]

    clusters = [
        DetectedSiteCluster(*_cluster_center(group), tuple(group))
        for group in groups
    ]
    clusters.sort(
        key=lambda cluster: (
            round(cluster.center_latitude, 10),
            round(cluster.center_longitude, 10),
            _canonical_path(cluster.records[0].file_path),
        )
    )
    return clusters


def filter_site_records(
    records: Iterable[ReceptionLogRecord | Mapping[str, Any]],
    site_id: int | None = None,
    date_start: date | None = None,
    date_end: date | None = None,
) -> list[ReceptionLogRecord]:
    result = []
    for raw_record in records:
        record = _coerce_record(raw_record)
        if site_id is not None and record.site_id != site_id:
            continue
        if date_start is not None and record.flight_date < date_start:
            continue
        if date_end is not None and record.flight_date > date_end:
            continue
        result.append(record)
    return sorted(result, key=lambda record: (record.flight_date, _canonical_path(record.file_path)))


def telemetry_channel_coverage(
    records: Iterable[ReceptionLogRecord | Mapping[str, Any]],
) -> list[TelemetryChannelCoverage]:
    normalized = [_coerce_record(record) for record in records]
    eligible = [record for record in normalized if record.status == "ok"]
    counts: dict[str, int] = defaultdict(int)
    for record in eligible:
        for channel in set(record.channels):
            counts[channel] += 1
    return [
        TelemetryChannelCoverage(channel, count, len(eligible))
        for channel, count in sorted(counts.items(), key=lambda item: item[0].casefold())
    ]


def refresh_reception_index(
    logs: Iterable[LibraryLogInfo],
    cached_records: Iterable[ReceptionLogRecord | Mapping[str, Any]],
    library_root: str | Path,
    sample_stride: int = SAMPLE_STRIDE,
    progress: ProgressCallback | None = None,
    is_cancelled: CancellationCheck | None = None,
) -> ReceptionScanResult:
    root = Path(library_root).resolve(strict=False)
    root_key = _canonical_path(root)
    cached = {
        _canonical_path(record.file_path): record
        for raw_record in cached_records
        for record in [_coerce_record(raw_record)]
        if _canonical_path(record.library_root) == root_key
    }
    log_items = sorted(list(logs), key=lambda item: _canonical_path(item.path))
    records: list[ReceptionLogRecord] = []
    scanned_count = 0
    cached_count = 0
    total = len(log_items)
    for completed, item in enumerate(log_items, start=1):
        if is_cancelled is not None and is_cancelled():
            return ReceptionScanResult(
                tuple(records),
                tuple(cluster_reception_records(records)),
                scanned_count,
                cached_count,
                sum(record.status in {"malformed", "io_error"} for record in records),
                True,
            )
        path = Path(item.path)
        metadata_from_library = False
        try:
            size, mtime_ns, _ = _file_metadata(path)
        except OSError:
            size, mtime_ns = int(item.size), int(float(item.modified) * 1_000_000_000)
            metadata_from_library = True
        existing = cached.get(_canonical_path(path))
        if (
            existing is not None
            and existing.file_size == size
            and existing.mtime_ns == mtime_ns
            and existing.index_version == RECEPTION_INDEX_VERSION
        ):
            record = existing
            cached_count += 1
        else:
            record = index_log(path, root, sample_stride=sample_stride)
            if metadata_from_library and record.status == "io_error":
                # Preserve the last metadata fingerprint supplied by the
                # library scan. Otherwise an inaccessible cloud placeholder
                # would be stored as 0/0 and retried on every refresh despite
                # remaining unchanged.
                record = replace(record, file_size=size, mtime_ns=mtime_ns)
            scanned_count += 1
        records.append(record)
        if progress is not None:
            progress(completed, total, path.name)
    clusters = cluster_reception_records(records, tolerance_km=SITE_TOLERANCE_KM)
    error_count = sum(record.status in {"malformed", "io_error"} for record in records)
    return ReceptionScanResult(
        tuple(records), tuple(clusters), scanned_count, cached_count, error_count, False
    )


def _local_xy(latitude: float, longitude: float, center: tuple[float, float]) -> tuple[float, float]:
    center_latitude, center_longitude = center
    y = EARTH_RADIUS_METERS * math.radians(latitude - center_latitude)
    x = EARTH_RADIUS_METERS * math.cos(math.radians(center_latitude)) * math.radians(longitude - center_longitude)
    return x, y


def _local_lon_lat(x: float, y: float, center: tuple[float, float]) -> tuple[float, float]:
    center_latitude, center_longitude = center
    latitude = center_latitude + math.degrees(y / EARTH_RADIUS_METERS)
    longitude_scale = max(1e-9, math.cos(math.radians(center_latitude)))
    longitude = center_longitude + math.degrees(x / (EARTH_RADIUS_METERS * longitude_scale))
    return longitude, latitude


def _telemetry_unit(column: str) -> str:
    match = re.search(r"\(([^()]*)\)\s*$", column)
    return match.group(1).strip() if match else ""


def build_reception_heatmap(
    records: Iterable[ReceptionLogRecord | Mapping[str, Any]],
    telemetry_column: str,
    site_center: tuple[float, float],
    cell_size_m: float = DEFAULT_CELL_SIZE_METERS,
    progress: ProgressCallback | None = None,
    is_cancelled: CancellationCheck | None = None,
) -> dict[str, object]:
    """Aggregate full-log reception values into observed equal-flight cells."""
    normalized_records = [_coerce_record(record) for record in records]
    cell_size = float(cell_size_m)
    if not math.isfinite(cell_size) or cell_size <= 0.0:
        raise ValueError("cell_size_m must be a positive finite number")
    if (
        len(site_center) != 2
        or not all(math.isfinite(float(value)) for value in site_center)
        or not -90.0 <= float(site_center[0]) <= 90.0
        or not -180.0 <= float(site_center[1]) <= 180.0
    ):
        raise ValueError("site_center must contain a finite latitude and longitude")
    site_center = (float(site_center[0]), float(site_center[1]))
    flight_medians: dict[tuple[int, int], list[float]] = defaultdict(list)
    sample_counts: dict[tuple[int, int], int] = defaultdict(int)
    logs_used = 0
    missing_channel_count = 0
    error_count = 0
    off_site_sample_count = 0
    total = len(normalized_records)

    for completed, record in enumerate(normalized_records, start=1):
        if is_cancelled is not None and is_cancelled():
            return {
                "status": "cancelled",
                "message": "Reception heatmap generation was cancelled.",
                "telemetry_column": telemetry_column,
                "cells": [],
                "value_min": None,
                "value_max": None,
                "cell_size_m": cell_size,
                "logs_considered": total,
                "logs_used": logs_used,
                "date_inferred_count": sum(item.date_inferred for item in normalized_records),
                "missing_channel_count": missing_channel_count,
                "error_count": error_count,
            }
        if telemetry_column not in record.channels:
            missing_channel_count += 1
            if progress is not None:
                progress(completed, total, record.file_path.name)
            continue
        try:
            log = load_log(record.file_path, record.library_root)
            gps = log.gps_columns
            if gps is None or telemetry_column not in log.dataframe.columns:
                missing_channel_count += 1
                continue
            candidates = _valid_gps_candidates(
                log.dataframe,
                gps.latitude,
                gps.longitude,
                time_values=list(log.time) if log.time is not None else None,
                elapsed_values=relative_seconds(log),
                value_column=telemetry_column,
            )
            site_candidates = [
                candidate
                for candidate in candidates
                if _haversine_km(
                    site_center[0],
                    site_center[1],
                    float(candidate[0]["lat"]),
                    float(candidate[0]["lon"]),
                )
                <= HEATMAP_SITE_RADIUS_KM
            ]
            off_site_sample_count += len(candidates) - len(site_candidates)
            values_by_cell: dict[tuple[int, int], list[float]] = defaultdict(list)
            for point, _ in site_candidates:
                value = point.get("value")
                if value is None or not math.isfinite(float(value)):
                    continue
                x, y = _local_xy(float(point["lat"]), float(point["lon"]), site_center)
                # Anchor the local grid on the flying-site center so (0, 0)
                # lies at the center of a cell rather than on four boundaries.
                key = (
                    math.floor((x + cell_size / 2.0) / cell_size),
                    math.floor((y + cell_size / 2.0) / cell_size),
                )
                values_by_cell[key].append(float(value))
            if values_by_cell:
                logs_used += 1
                for key, values in values_by_cell.items():
                    flight_medians[key].append(float(statistics.median(values)))
                    sample_counts[key] += len(values)
        except Exception:
            error_count += 1
        finally:
            if progress is not None:
                progress(completed, total, record.file_path.name)

    cells: list[ReceptionCell] = []
    for (x_index, y_index), medians in sorted(flight_medians.items()):
        center_x = x_index * cell_size
        center_y = y_index * cell_size
        x_min = center_x - cell_size / 2.0
        y_min = center_y - cell_size / 2.0
        x_max = center_x + cell_size / 2.0
        y_max = center_y + cell_size / 2.0
        center_lon, center_lat = _local_lon_lat(center_x, center_y, site_center)
        polygon = (
            _local_lon_lat(x_min, y_min, site_center),
            _local_lon_lat(x_max, y_min, site_center),
            _local_lon_lat(x_max, y_max, site_center),
            _local_lon_lat(x_min, y_max, site_center),
            _local_lon_lat(x_min, y_min, site_center),
        )
        cells.append(
            ReceptionCell(
                center_lat,
                center_lon,
                polygon,
                float(statistics.median(medians)),
                sample_counts[(x_index, y_index)],
                len(medians),
            )
        )

    cell_payload = [
        {
            "latitude": cell.latitude,
            "longitude": cell.longitude,
            "polygon": [[longitude, latitude] for longitude, latitude in cell.polygon],
            "value": cell.value,
            "sample_count": cell.sample_count,
            "flight_count": cell.flight_count,
        }
        for cell in cells
    ]
    values = [cell.value for cell in cells]
    status = "ok" if cells else "empty"
    message = "" if cells else "No valid GPS and telemetry samples matched the selected filters."
    return {
        "status": status,
        "message": message,
        "telemetry_column": telemetry_column,
        "telemetry_unit": _telemetry_unit(telemetry_column),
        "cells": cell_payload,
        "value_min": min(values) if values else None,
        "value_max": max(values) if values else None,
        "cell_size_m": cell_size,
        "logs_considered": total,
        "logs_used": logs_used,
        "date_inferred_count": sum(record.date_inferred for record in normalized_records),
        "missing_channel_count": missing_channel_count,
        "error_count": error_count,
        "off_site_sample_count": off_site_sample_count,
    }
