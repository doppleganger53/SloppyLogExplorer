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
    _NON_POSITION_GPS_TOKENS,
    _clean_columns,
    _coerce_numeric_series,
    _deduplicate_columns,
    _detect_time,
    _has_coordinate_name_hint,
    _has_date_header,
    _has_position_header_hint,
    _numeric_columns,
    _parse_coordinate_text,
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
VIEWPORT_MAX_RADIUS_METERS = 2_000.0
_VIEWPORT_BUCKET_METERS = 250.0
_OBSERVED_NUMERIC_COLUMNS_ATTR = "sloppy_observed_numeric_columns"
_INDEXED_CHANNEL_SUFFIX_RE = re.compile(r"^(?P<base>.+)\.(?P<index>[1-9]\d*)$")
_CALENDAR_LITERAL_RE = re.compile(
    r"(?:\b\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\b|\b\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}\b)"
)

ProgressCallback = Callable[[int, int, str], None]
CancellationCheck = Callable[[], bool]


def logical_telemetry_groups(channels: Iterable[str]) -> dict[str, tuple[str, ...]]:
    """Group pandas-style ``Name.1`` aliases without merging lone literals."""
    names = list(dict.fromkeys(str(channel) for channel in channels))
    name_set = set(names)
    indexed: dict[str, list[str]] = defaultdict(list)
    for name in names:
        match = _INDEXED_CHANNEL_SUFFIX_RE.fullmatch(name)
        if match is not None:
            indexed[match.group("base")].append(name)
    grouped_bases = {
        base
        for base, variants in indexed.items()
        if base in name_set or len(variants) > 1
    }
    groups: dict[str, list[str]] = {}
    for name in names:
        match = _INDEXED_CHANNEL_SUFFIX_RE.fullmatch(name)
        logical_name = (
            match.group("base")
            if match is not None and match.group("base") in grouped_bases
            else name
        )
        groups.setdefault(logical_name, []).append(name)
    return {name: tuple(members) for name, members in groups.items()}


def telemetry_group_columns(channels: Iterable[str], logical_name: str) -> tuple[str, ...]:
    """Resolve one logical item to its exact and terminal dot-number columns."""
    resolved: list[str] = []
    for raw_channel in channels:
        channel = str(raw_channel)
        match = _INDEXED_CHANNEL_SUFFIX_RE.fullmatch(channel)
        if channel == logical_name or (match is not None and match.group("base") == logical_name):
            resolved.append(channel)
    return tuple(dict.fromkeys(resolved))


class _ReceptionScanCancelled(RuntimeError):
    """Stop an in-progress file scan without caching a partial record."""


def _canonical_path(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).expanduser().resolve(strict=False)))


def _row_position_signature(
    row: Sequence[str],
    position_indexes: Sequence[int],
) -> tuple[float, float] | None:
    """Return a stable signature for a plausible joint position fix."""
    def signature(latitude: float, longitude: float) -> tuple[float, float]:
        wrapped_longitude = ((longitude + 180.0) % 360.0) - 180.0
        return round(latitude, 7), round(wrapped_longitude, 7)

    numeric_values: list[float] = []
    for index in position_indexes:
        text = str(row[index]).strip()
        if not text:
            continue
        parsed = _parse_coordinate_text(text)
        if parsed is not None:
            latitude, longitude, _altitude = parsed
            if (
                -90.0 <= latitude <= 90.0
                and -180.0 <= longitude <= 180.0
                and (abs(latitude) > 1e-9 or abs(longitude) > 1e-9)
            ):
                return signature(latitude, longitude)
        try:
            numeric = float(text)
        except ValueError:
            continue
        if math.isfinite(numeric):
            numeric_values.append(numeric)
    for latitude_index, latitude in enumerate(numeric_values):
        for longitude_index, longitude in enumerate(numeric_values):
            if latitude_index == longitude_index:
                continue
            if (
                -90.0 <= latitude <= 90.0
                and -180.0 <= longitude <= 180.0
                and (abs(latitude) > 1e-9 or abs(longitude) > 1e-9)
            ):
                return signature(latitude, longitude)
    return None


def _sampled_csv(
    path: Path,
    sample_stride: int,
    *,
    max_records: int | None = None,
    full_scan_if_position_hint: bool = False,
    is_cancelled: CancellationCheck | None = None,
) -> pd.DataFrame:
    """Read sparse data and GPS-event rows while retaining column evidence."""
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
        selected_rows: dict[int, list[str]] = {}
        short_log_rows: list[tuple[int, list[str]]] = []
        position_short_rows: list[tuple[int, list[str]]] = []
        position_sample_rows: list[tuple[int, list[str]]] = []
        resolved_position_short_rows: list[tuple[int, list[str]]] = []
        resolved_position_sample_rows: list[tuple[int, list[str]]] = []
        position_indexes = [
            index
            for index, column in enumerate(columns)
            if _has_coordinate_name_hint(column)
            and not (
                "gps" in column.lower()
                and any(token in column.lower() for token in _NON_POSITION_GPS_TOKENS)
            )
        ]
        observed_numeric_columns: set[str] = set()
        data_index = 0
        position_index = 0
        resolved_position_index = 0
        for row in reader:
            if is_cancelled is not None and is_cancelled():
                raise _ReceptionScanCancelled
            if not row:
                continue
            if record_limit is not None and data_index >= record_limit:
                break
            if len(row) < width:
                row = row + [""] * (width - len(row))
            elif len(row) > width:
                row = row[:width]
            for column, value in zip(columns, row):
                if column in observed_numeric_columns:
                    continue
                try:
                    numeric_value = float(value.strip())
                except (TypeError, ValueError):
                    continue
                if math.isfinite(numeric_value):
                    observed_numeric_columns.add(column)
            if data_index < stride:
                # Logs shorter than one stride are cheap to retain in full and
                # need at least two rows for the shared GPS detector to prove a
                # coordinate pair is telemetry rather than an incidental value.
                short_log_rows.append((data_index, row))
            if data_index % stride == 0:
                selected_rows[data_index] = row
            position_signature = _row_position_signature(row, position_indexes)
            if position_signature is not None:
                # Sample against the coordinate update cadence instead of the
                # absolute CSV row number. A sensor publishing on rows 1, 21,
                # 41, ... must not alias with a 20-row sample at phase zero.
                # Retaining every Nth update stays bounded while preserving the
                # relative weight of stationary and moving fix clouds.
                if position_index < stride:
                    position_short_rows.append((data_index, row))
                if position_index % stride == 0:
                    position_sample_rows.append((data_index, row))
                position_index += 1
            if position_signature is not None and all(
                abs(coordinate) > 1e-9 for coordinate in position_signature
            ):
                if resolved_position_index < stride:
                    resolved_position_short_rows.append((data_index, row))
                if resolved_position_index % stride == 0:
                    resolved_position_sample_rows.append((data_index, row))
                resolved_position_index += 1
            data_index += 1
        if data_index <= stride:
            selected_rows = dict(short_log_rows)
        elif position_index <= stride:
            selected_rows.update(position_short_rows)
        else:
            selected_rows.update(position_sample_rows)
        if resolved_position_index >= 2:
            # Absolute-stride rows can still contain repeated one-axis startup
            # locks. Once the full pass proves usable two-axis fixes exist,
            # blank only those coordinates before GPS outlier filtering can
            # mistake the startup placeholder for the dominant location. Keep
            # the rest of each row so the earliest date and channel evidence
            # remain available to the indexer.
            selected_rows = {
                index: (
                    [
                        "" if column_index in position_indexes else value
                        for column_index, value in enumerate(row)
                    ]
                    if (row_signature := _row_position_signature(row, position_indexes))
                    is not None
                    and (
                        abs(row_signature[0]) <= 1e-9
                        or abs(row_signature[1]) <= 1e-9
                    )
                    else row
                )
                for index, row in selected_rows.items()
            }
            if resolved_position_index <= stride:
                selected_rows.update(resolved_position_short_rows)
            else:
                selected_rows.update(resolved_position_sample_rows)
        rows = [row for _, row in sorted(selected_rows.items())]
    dataframe = pd.DataFrame(rows, columns=pd.Index(columns))
    dataframe.attrs[_OBSERVED_NUMERIC_COLUMNS_ATTR] = tuple(
        column for column in columns if column in observed_numeric_columns
    )
    return dataframe


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
    observed_numeric_columns = tuple(
        str(column)
        for column in dataframe.attrs.get(_OBSERVED_NUMERIC_COLUMNS_ATTR, ())
    )
    dataframe = _clean_columns(dataframe)
    _validate_table_structure(dataframe, file_path)
    parsed_time, timeline_columns = _detect_time(dataframe)
    numeric_columns = list(
        dict.fromkeys(
            (
                *_numeric_columns(dataframe, timeline_columns),
                *(column for column in observed_numeric_columns if column in dataframe.columns),
            )
        )
    )
    dataframe = dataframe.copy()
    gps = detect_gps_columns(dataframe, numeric_columns)
    numeric_columns = list(
        dict.fromkeys(
            (
                *_numeric_columns(dataframe, timeline_columns),
                *(column for column in observed_numeric_columns if column in dataframe.columns),
            )
        )
    )
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
    value_values: Sequence[object] | pd.Series | None = None,
) -> list[GpsCandidate]:
    if value_column is not None and value_values is not None:
        raise ValueError("provide value_column or value_values, not both")
    latitude = _coerce_numeric_series(dataframe[latitude_column])
    longitude = _coerce_numeric_series(dataframe[longitude_column])
    if value_column is not None:
        values = _coerce_numeric_series(dataframe[value_column])
    elif value_values is not None:
        raw_values = value_values if isinstance(value_values, pd.Series) else pd.Series(value_values)
        values = _coerce_numeric_series(raw_values).reset_index(drop=True)
    else:
        values = None
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
    is_cancelled: CancellationCheck | None = None,
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
            is_cancelled=is_cancelled,
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
                _sampled_csv(file_path, sample_stride, is_cancelled=is_cancelled),
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
    except _ReceptionScanCancelled:
        raise
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
            and (
                existing.index_version == RECEPTION_INDEX_VERSION
                or existing.status in {"malformed", "io_error"}
            )
        ):
            record = existing
            cached_count += 1
        else:
            try:
                record = index_log(
                    path,
                    root,
                    sample_stride=sample_stride,
                    is_cancelled=is_cancelled,
                )
            except _ReceptionScanCancelled:
                return ReceptionScanResult(
                    tuple(records),
                    tuple(cluster_reception_records(records)),
                    scanned_count,
                    cached_count,
                    sum(record.status in {"malformed", "io_error"} for record in records),
                    True,
                )
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
    longitude_delta = ((longitude - center_longitude + 180.0) % 360.0) - 180.0
    x = (
        EARTH_RADIUS_METERS
        * math.cos(math.radians(center_latitude))
        * math.radians(longitude_delta)
    )
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


def _row_median(dataframe: pd.DataFrame, columns: Sequence[str]) -> pd.Series:
    numeric_columns: list[pd.Series] = []
    for column in columns:
        numeric = _coerce_numeric_series(dataframe[column])
        finite = numeric.map(lambda value: bool(pd.notna(value) and math.isfinite(float(value))))
        numeric_columns.append(numeric.where(finite))
    if not numeric_columns:
        return pd.Series(math.nan, index=dataframe.index, dtype="float64")
    return pd.concat(numeric_columns, axis=1).median(axis=1, skipna=True)


def _replace_zero_references_with_lowest_positive(reference_values: pd.Series) -> pd.Series:
    """Treat zero power samples as the lowest positive power in this log."""
    positive_values = reference_values[reference_values.notna() & reference_values.gt(0.0)]
    if positive_values.empty:
        return reference_values
    replacement = positive_values.min()
    if pd.isna(replacement):
        return reference_values
    return reference_values.mask(reference_values.eq(0.0), float(replacement))


def _normalization_label(
    source: str,
    reference: str | None,
    mode: str | None,
    reference_bounds: tuple[float, float] | None,
) -> str:
    if reference is None or mode is None:
        return source
    if mode == "ratio":
        if reference_bounds is None:
            return f"{source} ÷ {reference}"
        lower, upper = reference_bounds
        return f"{source} ÷ clamp({reference}, {lower:g}, {upper:g})"
    if mode == "difference":
        return f"{source} − {reference}"
    return f"{source} − 10·log10({reference})"


def _normalized_unit(source: str, reference: str | None, mode: str | None) -> str:
    source_unit = _telemetry_unit(source)
    if reference is None or mode is None:
        return source_unit
    reference_unit = _telemetry_unit(reference)
    if mode == "difference" and source_unit and source_unit == reference_unit:
        return source_unit
    if mode == "db_power" and "db" in source_unit.casefold():
        return source_unit
    return ""


def _reception_viewport_focus(
    cell_points: Sequence[tuple[float, float, int, int]],
    site_center: tuple[float, float],
    radius_m: float = VIEWPORT_MAX_RADIUS_METERS,
) -> dict[str, object] | None:
    """Locate the densest observed sample neighborhood for the map camera."""
    requested_radius = float(radius_m)
    if not math.isfinite(requested_radius) or requested_radius <= 0.0:
        raise ValueError("radius_m must be a positive finite number")
    radius = min(requested_radius, VIEWPORT_MAX_RADIUS_METERS)
    points = sorted(
        (
            float(x),
            float(y),
            max(1, int(sample_count)),
            max(1, int(flight_count)),
        )
        for x, y, sample_count, flight_count in cell_points
        if math.isfinite(float(x)) and math.isfinite(float(y))
    )
    if not points:
        return None

    # Aggregate into small local buckets before scoring neighborhoods. The
    # heatmap itself can contain thousands of 5 m cells, while the selected
    # site's candidate area is bounded to 10 km from its center. This keeps the
    # density search effectively linear without making the camera sensitive to
    # input order.
    buckets: dict[tuple[int, int], list[float]] = {}
    for x, y, sample_weight, flight_weight in points:
        key = (
            math.floor(x / _VIEWPORT_BUCKET_METERS),
            math.floor(y / _VIEWPORT_BUCKET_METERS),
        )
        aggregate = buckets.setdefault(key, [0.0, 0.0, 0.0, 0.0, 0.0])
        aggregate[0] += sample_weight
        aggregate[1] += flight_weight
        aggregate[2] += 1.0
        aggregate[3] += x * sample_weight
        aggregate[4] += y * sample_weight

    bucket_centers = {
        key: (
            aggregate[3] / aggregate[0],
            aggregate[4] / aggregate[0],
            aggregate[0],
            aggregate[1],
            aggregate[2],
        )
        for key, aggregate in buckets.items()
    }
    neighbor_span = math.ceil(radius / _VIEWPORT_BUCKET_METERS) + 1
    best_score: tuple[float, float, float, float, float, float] | None = None
    best_center: tuple[float, float] | None = None
    for key in sorted(bucket_centers):
        center_x, center_y, _samples, _flights, _cells = bucket_centers[key]
        sample_kernel = 0.0
        covered_samples = 0.0
        flight_kernel = 0.0
        covered_flights = 0.0
        covered_cells = 0.0
        squared_distance_sum = 0.0
        for x_offset in range(-neighbor_span, neighbor_span + 1):
            for y_offset in range(-neighbor_span, neighbor_span + 1):
                neighbor = bucket_centers.get((key[0] + x_offset, key[1] + y_offset))
                if neighbor is None:
                    continue
                other_x, other_y, sample_weight, flight_weight, cell_weight = neighbor
                distance = math.hypot(other_x - center_x, other_y - center_y)
                if distance > radius:
                    continue
                kernel = 1.0 - (distance / radius) ** 2
                sample_kernel += sample_weight * kernel
                covered_samples += sample_weight
                flight_kernel += flight_weight * kernel
                covered_flights += flight_weight
                covered_cells += cell_weight
                squared_distance_sum += sample_weight * distance * distance
        rms_distance = math.sqrt(squared_distance_sum / covered_samples) if covered_samples else radius
        score = (
            sample_kernel,
            covered_samples,
            flight_kernel,
            covered_flights,
            covered_cells,
            -rms_distance,
        )
        candidate_center = (center_x, center_y)
        if (
            best_score is None
            or score > best_score
            or (score == best_score and (best_center is None or candidate_center < best_center))
        ):
            best_score = score
            best_center = candidate_center

    if best_center is None:
        return None

    # Refine the winning bucket with a bounded flat-kernel mean shift. Sample
    # count is the primary observation weight; flight count is retained for
    # diagnostics and deterministic secondary scoring above.
    focus_x, focus_y = best_center
    members: list[tuple[float, float, int, int]] = []
    for _ in range(8):
        members = [
            point
            for point in points
            if math.hypot(point[0] - focus_x, point[1] - focus_y) <= radius + 1e-9
        ]
        if not members:
            members = [min(points, key=lambda point: (math.hypot(point[0] - focus_x, point[1] - focus_y), point))]
        total_weight = sum(point[2] for point in members)
        next_x = sum(point[0] * point[2] for point in members) / total_weight
        next_y = sum(point[1] * point[2] for point in members) / total_weight
        shift = math.hypot(next_x - focus_x, next_y - focus_y)
        focus_x, focus_y = next_x, next_y
        if shift < 0.05:
            break

    members = [
        point
        for point in points
        if math.hypot(point[0] - focus_x, point[1] - focus_y) <= radius + 1e-9
    ] or members
    focus_longitude, focus_latitude = _local_lon_lat(focus_x, focus_y, site_center)
    focus_longitude = ((focus_longitude + 180.0) % 360.0) - 180.0
    member_samples = sum(point[2] for point in members)
    rms_distance = math.sqrt(
        sum(
            point[2] * ((point[0] - focus_x) ** 2 + (point[1] - focus_y) ** 2)
            for point in members
        )
        / member_samples
    )
    return {
        "latitude": focus_latitude,
        "longitude": focus_longitude,
        "radius_m": radius,
        "cell_count": len(members),
        "sample_count": member_samples,
        "flight_count": sum(point[3] for point in members),
        "rms_distance_m": rms_distance,
    }


def build_reception_heatmap(
    records: Iterable[ReceptionLogRecord | Mapping[str, Any]],
    telemetry_column: str,
    site_center: tuple[float, float],
    cell_size_m: float = DEFAULT_CELL_SIZE_METERS,
    progress: ProgressCallback | None = None,
    is_cancelled: CancellationCheck | None = None,
    reference_column: str | None = None,
    normalization_mode: str | None = None,
    reference_range: tuple[float, float] | None = None,
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
    if normalization_mode not in {None, "ratio", "difference", "db_power"}:
        raise ValueError("normalization_mode must be ratio, difference, db_power, or None")
    if (reference_column is None) != (normalization_mode is None):
        raise ValueError("reference_column and normalization_mode must be provided together")
    if reference_column == telemetry_column and reference_column is not None:
        raise ValueError("reference_column must differ from telemetry_column")
    applied_reference_range: tuple[float, float] | None = None
    if normalization_mode == "ratio" and reference_range is not None:
        if len(reference_range) != 2 or not all(math.isfinite(float(value)) for value in reference_range):
            raise ValueError("reference_range must contain two finite values")
        left, right = (float(reference_range[0]), float(reference_range[1]))
        applied_reference_range = (min(left, right), max(left, right))
    flight_medians: dict[tuple[int, int], list[float]] = defaultdict(list)
    sample_counts: dict[tuple[int, int], int] = defaultdict(int)
    logs_used = 0
    missing_channel_count = 0
    missing_reference_count = 0
    invalid_reference_sample_count = 0
    clamped_reference_sample_count = 0
    observed_reference_min: float | None = None
    observed_reference_max: float | None = None
    error_count = 0
    off_site_sample_count = 0
    total = len(normalized_records)

    for completed, record in enumerate(normalized_records, start=1):
        if is_cancelled is not None and is_cancelled():
            return {
                "status": "cancelled",
                "message": "Reception heatmap generation was cancelled.",
                "telemetry_column": telemetry_column,
                "reference_column": reference_column,
                "normalization_mode": normalization_mode,
                "cells": [],
                "value_min": None,
                "value_max": None,
                "cell_size_m": cell_size,
                "logs_considered": total,
                "logs_used": logs_used,
                "date_inferred_count": sum(item.date_inferred for item in normalized_records),
                "missing_channel_count": missing_channel_count,
                "missing_reference_count": missing_reference_count,
                "invalid_reference_sample_count": invalid_reference_sample_count,
                "clamped_reference_sample_count": clamped_reference_sample_count,
                "error_count": error_count,
            }
        indexed_source_columns = telemetry_group_columns(record.channels, telemetry_column)
        if not indexed_source_columns:
            missing_channel_count += 1
            if progress is not None:
                progress(completed, total, record.file_path.name)
            continue
        try:
            log = load_log(record.file_path, record.library_root)
            gps = log.gps_columns
            source_columns = telemetry_group_columns(log.dataframe.columns, telemetry_column)
            if gps is None or not source_columns:
                missing_channel_count += 1
                continue
            reference_columns: tuple[str, ...] = ()
            if reference_column is not None:
                if not telemetry_group_columns(record.channels, reference_column):
                    missing_reference_count += 1
                    continue
                reference_columns = telemetry_group_columns(log.dataframe.columns, reference_column)
                if not reference_columns:
                    missing_reference_count += 1
                    continue
            source_values = _row_median(log.dataframe, source_columns)
            transformed_values = source_values.copy()
            reference_values: pd.Series | None = None
            clamped_mask = pd.Series(False, index=log.dataframe.index)
            if reference_columns:
                reference_values = _row_median(log.dataframe, reference_columns)
                if normalization_mode == "ratio":
                    denominator = reference_values.copy()
                    if applied_reference_range is not None:
                        lower, upper = applied_reference_range
                        clamped_mask = reference_values.notna() & (
                            (reference_values < lower) | (reference_values > upper)
                        )
                        denominator = denominator.clip(lower=lower, upper=upper)
                    valid = source_values.notna() & denominator.notna() & denominator.ne(0.0)
                    transformed_values = (source_values / denominator).where(valid)
                elif normalization_mode == "difference":
                    valid = source_values.notna() & reference_values.notna()
                    transformed_values = (source_values - reference_values).where(valid)
                else:
                    normalization_reference_values = _replace_zero_references_with_lowest_positive(
                        reference_values
                    )
                    valid = (
                        source_values.notna()
                        & normalization_reference_values.notna()
                        & normalization_reference_values.gt(0.0)
                    )
                    transformed_values = (
                        source_values - 10.0 * normalization_reference_values.map(
                            lambda value: math.log10(float(value)) if pd.notna(value) and float(value) > 0.0 else math.nan
                        )
                    ).where(valid)
            candidates = _valid_gps_candidates(
                log.dataframe,
                gps.latitude,
                gps.longitude,
                time_values=list(log.time) if log.time is not None else None,
                elapsed_values=relative_seconds(log),
                value_values=transformed_values,
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
            if reference_values is not None:
                for point, _ in site_candidates:
                    row_index = int(point["row"]) - 1
                    source_value = _coerce_float(source_values.iloc[row_index])
                    if source_value is None:
                        continue
                    reference_value = _coerce_float(reference_values.iloc[row_index])
                    if reference_value is not None:
                        observed_reference_min = (
                            reference_value
                            if observed_reference_min is None
                            else min(observed_reference_min, reference_value)
                        )
                        observed_reference_max = (
                            reference_value
                            if observed_reference_max is None
                            else max(observed_reference_max, reference_value)
                        )
                    if _coerce_float(transformed_values.iloc[row_index]) is None:
                        invalid_reference_sample_count += 1
                    if bool(clamped_mask.iloc[row_index]):
                        clamped_reference_sample_count += 1
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
    viewport_points: list[tuple[float, float, int, int]] = []
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
        viewport_points.append(
            (
                center_x,
                center_y,
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
    effective_reference_range = applied_reference_range
    if normalization_mode == "ratio" and effective_reference_range is None:
        if observed_reference_min is not None and observed_reference_max is not None:
            effective_reference_range = (observed_reference_min, observed_reference_max)
    payload: dict[str, object] = {
        "status": status,
        "message": message,
        "telemetry_column": telemetry_column,
        "telemetry_label": _normalization_label(
            telemetry_column,
            reference_column,
            normalization_mode,
            effective_reference_range,
        ),
        "telemetry_unit": _normalized_unit(telemetry_column, reference_column, normalization_mode),
        "reference_column": reference_column,
        "normalization_mode": normalization_mode,
        "reference_auto_range": normalization_mode == "ratio" and reference_range is None,
        "reference_observed_min": observed_reference_min,
        "reference_observed_max": observed_reference_max,
        "reference_range_min": effective_reference_range[0] if effective_reference_range is not None else None,
        "reference_range_max": effective_reference_range[1] if effective_reference_range is not None else None,
        "cells": cell_payload,
        "value_min": min(values) if values else None,
        "value_max": max(values) if values else None,
        "cell_size_m": cell_size,
        "logs_considered": total,
        "logs_used": logs_used,
        "date_inferred_count": sum(record.date_inferred for record in normalized_records),
        "missing_channel_count": missing_channel_count,
        "missing_reference_count": missing_reference_count,
        "invalid_reference_sample_count": invalid_reference_sample_count,
        "clamped_reference_sample_count": clamped_reference_sample_count,
        "error_count": error_count,
        "off_site_sample_count": off_site_sample_count,
    }
    viewport_focus = _reception_viewport_focus(viewport_points, site_center)
    if viewport_focus is not None:
        payload["viewport_focus"] = viewport_focus
    return payload
