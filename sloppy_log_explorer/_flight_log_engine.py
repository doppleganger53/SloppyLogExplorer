"""Bundled deterministic RC flight-window detection and Markdown primitives.

Adapted from Kurt's user-authored maintain-rc-flight-log skill. See NOTICE.md.
Kept in-process so portable builds do not depend on a Codex skill installation.
Detection thresholds match the source implementation (seven reception swings).
"""

from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Iterator, Sequence, TextIO


SUPPORTED_EXTENSIONS = {".csv", ".log"}
CONTROL_NAMES = ("rudder", "elevator", "throttle", "aileron")
VFR_FAMILIES = ("rx", "2.4ghz", "lora", "900mhz")
VFR_LABELS = {"rx": "Minimum Rx VFR", "2.4ghz": "Minimum 2.4GHz VFR",
              "lora": "Minimum LoRa VFR", "900mhz": "Minimum 900MHz VFR"}
MIN_DIRECT_ALTITUDE_SPAN_M = 15.0
STRONG_DIRECT_ALTITUDE_SPAN_M = 25.0
MAX_GROUND_ALTITUDE_SPAN_M = 12.0
MIN_DIRECT_SPEED_MPH = 20.0
MIN_DIRECT_SPEED_SECONDS = 10
MIN_SPEED_ONLY_SECONDS = 30
MIN_INDIRECT_FLIGHT_SECONDS = 180
MIN_RECEPTION_ROBUST_SPAN = 15.0
MIN_RECEPTION_SWINGS = 7   
RSSI_SWING_THRESHOLD = 6.0
VFR_SWING_THRESHOLD = 10.0
MIN_VALID_TEMPERATURE_C = -100.0
MAX_VALID_TEMPERATURE_C = 1200.0
TABLE_HEADER = (
    "| Flight number | Date/time | Flight duration | Minimum Rx VFR | "
    "Minimum 2.4GHz VFR | Minimum LoRa VFR | Minimum 900MHz VFR | Notes |"
)
TABLE_SEPARATOR = "|---:|---|---:|---:|---:|---:|---:|---|"
LEGACY_TABLE_HEADERS = {
    "| Flight number | Date/time | Flight duration | Minimum RxVFR | "
    "Minimum 2.4GHz VFR | Minimum LoRa VFR | Minimum 900MHz VFR | Notes |"
}
FILENAME_TIMESTAMP_RE = re.compile(
    r"^(?P<plane>.+?)[-_ ](?P<date>\d{4}[-_]\d{2}[-_]\d{2})"
    r"(?:[-_ ]\d{2}[-_]\d{2}[-_]\d{2})?(?:\b|$)",
    re.IGNORECASE,
)


@dataclass
class RangeStats:
    minimum: float | None = None
    maximum: float | None = None
    count: int = 0
    low_count: int = 0

    def add(self, value: float, *, vfr: bool = False) -> None:
        if not math.isfinite(value):
            return
        if vfr and not 0.0 <= value <= 100.0:
            return
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)
        self.count += 1
        if vfr:
            if value <= 5.0:
                self.low_count += 1

    @property
    def span(self) -> float:
        if self.minimum is None or self.maximum is None:
            return 0.0
        return self.maximum - self.minimum

    def merge(self, other: "RangeStats") -> None:
        if other.minimum is not None:
            self.minimum = other.minimum if self.minimum is None else min(self.minimum, other.minimum)
        if other.maximum is not None:
            self.maximum = other.maximum if self.maximum is None else max(self.maximum, other.maximum)
        self.count += other.count
        self.low_count += other.low_count


@dataclass
class Bucket:
    index: int
    first_time: datetime
    last_time: datetime
    controls: dict[str, RangeStats] = field(default_factory=lambda: {name: RangeStats() for name in CONTROL_NAMES})
    vfr: dict[str, RangeStats] = field(default_factory=lambda: {name: RangeStats() for name in VFR_FAMILIES})
    # Capture policy must not change the evidence used to detect a flight.
    output_vfr: dict[str, RangeStats] = field(default_factory=lambda: {name: RangeStats() for name in VFR_FAMILIES})
    rssi: dict[str, RangeStats] = field(default_factory=dict)
    motion: RangeStats = field(default_factory=RangeStats)
    evidence: dict[str, RangeStats] = field(default_factory=dict)
    temperatures: dict[str, RangeStats] = field(default_factory=dict)
    sensor_maxima: dict[str, RangeStats] = field(default_factory=dict)
    throttle_active: int = 0
    throttle_samples: int = 0
    row_count: int = 0


@dataclass(frozen=True)
class Columns:
    date: int | None
    time: int | None
    combined_time: int | None
    controls: dict[str, int]
    vfr: dict[str, list[int]]
    rssi: list[int]
    motion: list[tuple[int, str]]
    evidence: list[tuple[int, str, float]]
    temperatures: list[tuple[int, str, str]]


@dataclass(frozen=True)
class Flight:
    plane: str
    source: Path
    start: datetime
    end: datetime
    vfr_minimums: dict[str, float | None]
    notes: str
    temperature_maximums: dict[str, float] = field(default_factory=dict)
    confidence: str = "confirmed"
    evidence: str = ""
    sensor_maximums: dict[str, float] = field(default_factory=dict)
    vfr_present: tuple[str, ...] | None = None

    @property
    def duration_seconds(self) -> int:
        return max(0, int(round((self.end - self.start).total_seconds())))

    @property
    def display_datetime(self) -> str:
        return self.start.strftime("%Y-%m-%d %H:%M:%S")

    @property
    def display_duration(self) -> str:
        seconds = self.duration_seconds
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


@dataclass
class FileAnalysis:
    path: Path
    plane: str
    flights: list[Flight]
    status: str
    detail: str = ""
    rejected_candidates: list[dict[str, str]] = field(default_factory=list)
    archive_interval: tuple[datetime, datetime] | None = None


@dataclass(frozen=True)
class ExistingRow:
    number: int
    timestamp: datetime
    duration: str
    notes: str


def normalize_header(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\ufeff", "").strip()).lower()


def deduplicate_headers(headers: Sequence[str]) -> list[str]:
    counts: dict[str, int] = {}
    result: list[str] = []
    for index, raw in enumerate(headers):
        name = raw.replace("\ufeff", "").strip() or f"Unnamed: {index}"
        count = counts.get(name, 0)
        counts[name] = count + 1
        result.append(name if count == 0 else f"{name}.{count}")
    return result


def parse_number(value: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def parse_timestamp(date_value: str | None, time_value: str | None, combined: str | None) -> datetime | None:
    candidates: list[str] = []
    if date_value and time_value:
        candidates.append(f"{date_value.strip()} {time_value.strip()}")
    if combined:
        candidates.append(combined.strip())
    for candidate in candidates:
        if not candidate:
            continue
        cleaned = candidate.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(cleaned)
        except ValueError:
            pass
        for fmt in (
            "%Y/%m/%d %H:%M:%S.%f",
            "%Y/%m/%d %H:%M:%S",
            "%m/%d/%Y %H:%M:%S.%f",
            "%m/%d/%Y %H:%M:%S",
        ):
            try:
                return datetime.strptime(candidate, fmt)
            except ValueError:
                continue
    return None


def infer_plane(path: Path, scan_root: Path | None = None) -> str:
    match = FILENAME_TIMESTAMP_RE.match(path.stem)
    if match:
        plane = match.group("plane").strip(" -_")
        if plane:
            return plane
    if scan_root is not None:
        try:
            relative = path.relative_to(scan_root)
            if len(relative.parts) > 1:
                return relative.parts[0]
        except ValueError:
            pass
    return path.parent.name or "Unsorted"


def temperature_unit(header: str) -> str | None:
    name = normalize_header(header)
    if not re.search(r"(?:temp(?:erature)?|\bcht\b|\begt\b)", name):
        return None
    if re.search(r"(?:°\s*f\b|\(\s*f\s*\)|fahrenheit\b)", name):
        return "f"
    if re.search(r"(?:°\s*c\b|\(\s*c\s*\)|celsius\b)", name):
        return "c"
    return None


def valid_temperature(value: float, unit: str) -> bool:
    celsius = (value - 32.0) * 5.0 / 9.0 if unit == "f" else value
    return MIN_VALID_TEMPERATURE_C <= celsius <= MAX_VALID_TEMPERATURE_C


def servo_current_headers(headers: Sequence[str]) -> set[str]:
    """Match named current/temperature pairs while excluding known power devices.

    FBUS servo names are model-specific (e.g. ELE L curr(A), ELE L temp(°F)).
    Match their shared name, never substitute a generic receiver/pack current.
    """
    temperatures: set[tuple[str, str]] = set()
    currents: list[tuple[str, tuple[str, str]]] = []
    for header in headers:
        match = re.fullmatch(r"(.*?)\s*\(\s*(ma|a|°?\s*[cf])\s*\)(\.\d+)?", normalize_header(header))
        if match is None:
            continue
        name, unit, duplicate = match.groups()
        # Retain measurement indices: SRV curr1 and SRV temp1 are one pair,
        # distinct from curr2/temp2 and from duplicate source columns (.1).
        name = re.sub(r"(?:current|curr|cur|temperature|temp)\s*(\d*)$", r" \1", name).strip()
        name = re.sub(r"^(?:current|curr|cur|temperature|temp)\b", "", name).strip()
        if not any(character.isalpha() for character in name):
            continue
        if re.search(r"\b(?:esc|s?bec|battery|batt|pack|rx|receiver|tx|motor|engine|turbine)\d*\b", name):
            continue
        identity = (re.sub(r"[\W_]+", "", name), duplicate or "")
        if unit in {"a", "ma"}:
            currents.append((header, identity))
        else:
            temperatures.add(identity)
    return {header for header, identity in currents if identity in temperatures}


def classify_columns(headers: Sequence[str]) -> Columns:
    normalized = [normalize_header(header) for header in headers]
    date_index = next((i for i, name in enumerate(normalized) if name == "date"), None)
    time_index = next((i for i, name in enumerate(normalized) if name == "time"), None)
    combined_index = next(
        (i for i, name in enumerate(normalized) if "datetime" in name or "timestamp" in name),
        None,
    )
    controls: dict[str, int] = {}
    for control in CONTROL_NAMES:
        index = next(
            (
                i
                for i, name in enumerate(normalized)
                if name == control or name.startswith(f"{control}(") or name.startswith(f"{control} ")
            ),
            None,
        )
        if index is not None:
            controls[control] = index

    vfr: dict[str, list[int]] = {family: [] for family in VFR_FAMILIES}
    rssi: list[int] = []
    motion: list[tuple[int, str]] = []
    evidence: list[tuple[int, str, float]] = []
    temperatures: list[tuple[int, str, str]] = []
    for index, name in enumerate(normalized):
        unit = temperature_unit(headers[index])
        if unit is not None:
            temperatures.append((index, headers[index], unit))
        if "vfr" in name:
            if "2.4" in name or "2g4" in name:
                vfr["2.4ghz"].append(index)
            elif "lora" in name or "lo ra" in name:
                vfr["lora"].append(index)
            elif "900" in name:
                vfr["900mhz"].append(index)
            elif name.startswith("rx vfr") or name in {"rxvfr", "vfr(%)", "vfr"}:
                vfr["rx"].append(index)
        if "rssi" in name and not name.startswith("tx rssi"):
            rssi.append(index)
        if "speed" in name and ("air" in name or "gps" in name):
            unit = "knots" if "knot" in name else "mph" if "mph" in name else "generic"
            motion.append((index, unit))
            scale = 1.15078 if unit == "knots" else 0.621371 if "km/h" in name or "kph" in name else 1.0
            family = "air_speed_mph" if "air" in name else "gps_speed_mph"
            evidence.append((index, family, scale))
        elif "rpm" in name and "consum" not in name:
            motion.append((index, "rpm"))
            evidence.append((index, "rpm", 1.0))
        if "gps alt" in name or name.startswith("altitude(") or name == "altitude":
            scale = 0.3048 if "ft" in name else 1.0
            evidence.append((index, "altitude_m", scale))
        if "vspeed" in name or "vertical speed" in name:
            scale = 0.00508 if "ft/min" in name or "fpm" in name else 1.0
            evidence.append((index, "vertical_speed_mps", scale))
        if re.match(r"acc[xyz](?:\(|\b)", name) and "(g" in name:
            evidence.append((index, f"accel_{name[3]}", 1.0))
        if "current(a)" in name and not any(token in name for token in ("rxcurrent", "rx current", "s.p current", "bec current")):
            evidence.append((index, "propulsion_current_a", 1.0))
        if "consum" in name and "mah" in name and not name.startswith("rx consum"):
            evidence.append((index, "propulsion_consumption_mah", 1.0))
    return Columns(date_index, time_index, combined_index, controls, vfr, rssi, motion, evidence, temperatures)


def throttle_is_active(value: float, header: str) -> bool:
    name = normalize_header(header)
    if "%" in name or (0.0 <= value <= 100.0 and "throttle" in name):
        return value > 15.0
    if -1100.0 <= value <= 1100.0:
        return value > -700.0
    return value > 0.15


def motion_is_active(value: float, unit: str) -> bool:
    if unit == "rpm":
        return value >= 1200.0
    if unit == "knots":
        return value >= 8.0
    if unit == "mph":
        return value >= 10.0
    return value >= 10.0


def row_value(row: Sequence[str], index: int | None) -> str | None:
    return row[index] if index is not None and index < len(row) else None


def open_csv(path: Path) -> tuple[Iterator[list[str]], TextIO]:
    handle = path.open("r", encoding="utf-8-sig", errors="replace", newline="")
    sample = handle.read(4096)
    handle.seek(0)
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return csv.reader(handle, dialect), handle


def contains_flight_start(path: Path, start: datetime) -> bool:
    """A detected window always starts at an observed wall-clock second."""
    reader, handle = open_csv(path)
    try:
        columns = classify_columns(deduplicate_headers(next(reader, [])))
        if columns.date is None and columns.time is None and columns.combined_time is None:
            return False
        for row in reader:
            timestamp = parse_timestamp(row_value(row, columns.date), row_value(row, columns.time),
                                        row_value(row, columns.combined_time))
            if timestamp is not None and timestamp.replace(tzinfo=None, microsecond=0) == start:
                return True
        return False
    finally:
        handle.close()


def load_buckets(path: Path, bucket_seconds: int, max_sensors: Sequence[str] = (), *,
                 exclude_sensors: Sequence[str] = (), auto_vfr: bool = True,
                 auto_temperatures: bool = True,
                 auto_servo_currents: bool = True) -> tuple[list[str], list[Bucket], str | None, bool]:
    reader, handle = open_csv(path)
    try:
        try:
            headers = deduplicate_headers(next(reader))
        except StopIteration:
            return [], [], "empty file", False
        columns = classify_columns(headers)
        excluded = {normalize_header(name) for name in exclude_sensors}
        requested = {normalize_header(name) for name in max_sensors}
        if auto_servo_currents:
            requested.update(normalize_header(name) for name in servo_current_headers(headers))
        maximum_indexes = [(i, name) for i, name in enumerate(headers) if normalize_header(name) in requested]
        if columns.date is None and columns.time is None and columns.combined_time is None:
            return headers, [], "no usable timestamp column", False
        if not all(control in columns.controls for control in CONTROL_NAMES):
            return headers, [], "missing one or more primary control columns", False
        if not any(columns.vfr.values()) and not columns.rssi:
            return headers, [], "no VFR or RSSI link-quality column", False

        start_time: datetime | None = None
        buckets_by_index: dict[int, Bucket] = {}
        complete = True
        for row in reader:
            if not row:
                continue
            if len(row) != len(headers):
                complete = False
            timestamp = parse_timestamp(
                row_value(row, columns.date),
                row_value(row, columns.time),
                row_value(row, columns.combined_time),
            )
            if timestamp is None:
                complete = False
                continue
            if start_time is None:
                start_time = timestamp
            if (timestamp.tzinfo is None) != (start_time.tzinfo is None):
                complete = False
                continue
            elapsed = (timestamp - start_time).total_seconds()
            if elapsed < 0:
                complete = False
                continue
            index = int(elapsed // bucket_seconds)
            bucket = buckets_by_index.get(index)
            if bucket is None:
                bucket = Bucket(index=index, first_time=timestamp, last_time=timestamp)
                buckets_by_index[index] = bucket
            else:
                bucket.first_time = min(bucket.first_time, timestamp)
                bucket.last_time = max(bucket.last_time, timestamp)
            bucket.row_count += 1

            for control, column_index in columns.controls.items():
                value = parse_number(row_value(row, column_index) or "")
                if value is None:
                    complete = False
                    continue
                bucket.controls[control].add(value)
                if control == "throttle":
                    bucket.throttle_samples += 1
                    if throttle_is_active(value, headers[column_index]):
                        bucket.throttle_active += 1
            has_link = False
            for family, indexes in columns.vfr.items():
                for column_index in indexes:
                    value = parse_number(row_value(row, column_index) or "")
                    if value is not None:
                        has_link |= 0 <= value <= 100
                        bucket.vfr[family].add(value, vfr=True)
                        if auto_vfr and normalize_header(headers[column_index]) not in excluded:
                            bucket.output_vfr[family].add(value, vfr=True)
            for column_index in columns.rssi:
                value = parse_number(row_value(row, column_index) or "")
                if value is None or not -150.0 <= value <= 120.0:
                    continue
                has_link = True
                key = normalize_header(headers[column_index])
                bucket.rssi.setdefault(key, RangeStats()).add(value)
            if not has_link:
                complete = False
            for column_index, unit in columns.motion:
                value = parse_number(row_value(row, column_index) or "")
                if value is not None and motion_is_active(value, unit):
                    bucket.motion.add(value)
            for column_index, family, scale in columns.evidence:
                value = parse_number(row_value(row, column_index) or "")
                if value is not None:
                    scaled = value * scale
                    if family == "vertical_speed_mps" and abs(scaled) > 50.0:
                        continue
                    bucket.evidence.setdefault(family, RangeStats()).add(scaled)
            for column_index, label, unit in columns.temperatures:
                value = parse_number(row_value(row, column_index) or "")
                if auto_temperatures and normalize_header(label) not in excluded and value is not None and valid_temperature(value, unit):
                    bucket.temperatures.setdefault(label, RangeStats()).add(value)
            for column_index, label in maximum_indexes:
                value = parse_number(row_value(row, column_index) or "")
                if normalize_header(label) not in excluded and value is not None and value != -1024:
                    bucket.sensor_maxima.setdefault(label, RangeStats()).add(value)
        return headers, [buckets_by_index[key] for key in sorted(buckets_by_index)], None, complete
    finally:
        handle.close()


def window_stats(buckets: Sequence[Bucket], center: int, radius: int) -> tuple[dict[str, RangeStats], dict[str, RangeStats], float, bool]:
    controls = {name: RangeStats() for name in CONTROL_NAMES}
    links: dict[str, RangeStats] = {name: RangeStats() for name in VFR_FAMILIES}
    rssi: dict[str, RangeStats] = {}
    throttle_active = 0
    throttle_samples = 0
    motion = False
    center_index = buckets[center].index
    left = center
    while left > 0 and buckets[left - 1].index >= center_index - radius:
        left -= 1
    right = center
    while right + 1 < len(buckets) and buckets[right + 1].index <= center_index + radius:
        right += 1
    for bucket in buckets[left : right + 1]:
        for name in CONTROL_NAMES:
            controls[name].merge(bucket.controls[name])
        for name in VFR_FAMILIES:
            links[name].merge(bucket.vfr[name])
        for name, stats in bucket.rssi.items():
            rssi.setdefault(name, RangeStats()).merge(stats)
        throttle_active += bucket.throttle_active
        throttle_samples += bucket.throttle_samples
        motion = motion or bucket.motion.count > 0
    links.update({f"rssi:{name}": stats for name, stats in rssi.items()})
    ratio = throttle_active / throttle_samples if throttle_samples else 0.0
    return controls, links, ratio, motion


def find_segments(
    buckets: Sequence[Bucket],
    *,
    bucket_seconds: int,
    rolling_seconds: int,
    merge_gap_seconds: int,
    min_flight_seconds: int,
) -> list[tuple[int, int]]:
    if not buckets:
        return []
    radius = max(1, int(round(rolling_seconds / bucket_seconds / 2)))
    seeds: list[int] = []
    for position, bucket in enumerate(buckets):
        controls, links, throttle_ratio, motion = window_stats(buckets, position, radius)
        axis_spans = [controls[name].span for name in ("rudder", "elevator", "aileron")]
        active_axes = sum(span >= 60.0 for span in axis_spans)
        control_dynamic = active_axes >= 2 or (max(axis_spans, default=0.0) >= 140.0 and sum(axis_spans) >= 220.0)
        vfr_dynamic = any(stats.count >= 2 and stats.span >= 3.0 for key, stats in links.items() if not key.startswith("rssi:"))
        rssi_dynamic = any(stats.count >= 2 and stats.span >= 3.0 for key, stats in links.items() if key.startswith("rssi:"))
        powered = throttle_ratio >= 0.20
        if control_dynamic and (vfr_dynamic or rssi_dynamic) and (powered or motion):
            seeds.append(position)
    if not seeds:
        return []

    groups: list[list[int]] = [[seeds[0]]]
    max_gap_buckets = max(1, math.ceil(merge_gap_seconds / bucket_seconds))
    for position in seeds[1:]:
        previous = groups[-1][-1]
        if buckets[position].index - buckets[previous].index <= max_gap_buckets:
            groups[-1].append(position)
        else:
            groups.append([position])

    segments: list[tuple[int, int]] = []
    extension = radius + max_gap_buckets
    for group in groups:
        search_start = max(0, group[0] - extension)
        search_end = min(len(buckets) - 1, group[-1] + extension)
        support = [
            position
            for position in range(search_start, search_end + 1)
            if buckets[position].throttle_active > 0 or buckets[position].motion.count > 0
        ]
        if not support:
            continue
        start_pos, end_pos = support[0], support[-1]
        duration = (buckets[end_pos].last_time - buckets[start_pos].first_time).total_seconds()
        if duration >= min_flight_seconds:
            if segments and buckets[start_pos].index - buckets[segments[-1][1]].index <= max_gap_buckets:
                segments[-1] = (segments[-1][0], end_pos)
            else:
                segments.append((start_pos, end_pos))
    return segments


def percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = int(round((len(ordered) - 1) * fraction))
    return ordered[max(0, min(position, len(ordered) - 1))]


def evidence_series(buckets: Sequence[Bucket], start_pos: int, end_pos: int, family: str) -> list[float]:
    values: list[float] = []
    for bucket in buckets[start_pos : end_pos + 1]:
        stats = bucket.evidence.get(family)
        if stats is not None and stats.minimum is not None and stats.maximum is not None:
            values.append((stats.minimum + stats.maximum) / 2.0)
    return values


def robust_span(values: Sequence[float]) -> float | None:
    low = percentile(values, 0.05)
    high = percentile(values, 0.95)
    return None if low is None or high is None else high - low


def archive_airborne_evidence(buckets: Sequence[Bucket]) -> str:
    """Keep recordings with airborne sensor evidence even without a detected control segment."""
    end = len(buckets) - 1
    for family in ("gps_speed_mph", "air_speed_mph"):
        speeds = evidence_series(buckets, 0, end, family)
        if sum(speed >= MIN_DIRECT_SPEED_MPH for speed in speeds) >= MIN_DIRECT_SPEED_SECONDS:
            return "sustained flight speed"
    if vertical_activity(buckets, 0, end):
        return "climb and descent"
    # Ignore zero altitude during GPS acquisition. Requiring three observed
    # buckets at each extreme discounts isolated spikes without diluting a short
    # flight in an otherwise long recording on the ground.
    altitudes = sorted(value for value in evidence_series(buckets, 0, end, "altitude_m") if value != 0)
    if len(altitudes) >= 6 and altitudes[-3] - altitudes[2] >= STRONG_DIRECT_ALTITUDE_SPAN_M:
        return "sustained altitude change"
    return ""


def count_directional_swings(values: Sequence[float], threshold: float) -> int:
    if len(values) < 2:
        return 0
    smoothed = [
        percentile(values[max(0, position - 2) : min(len(values), position + 3)], 0.5)
        for position in range(len(values))
    ]
    usable = [value for value in smoothed if value is not None]
    if len(usable) < 2:
        return 0

    anchor = usable[0]
    extreme = anchor
    direction = 0
    swings = 0
    for value in usable[1:]:
        if direction == 0:
            if value - anchor >= threshold:
                direction = 1
                extreme = value
            elif anchor - value >= threshold:
                direction = -1
                extreme = value
        elif direction > 0:
            if value > extreme:
                extreme = value
            elif extreme - value >= threshold:
                swings += 1
                direction = -1
                extreme = value
        else:
            if value < extreme:
                extreme = value
            elif value - extreme >= threshold:
                swings += 1
                direction = 1
                extreme = value
    return swings


def reception_dynamics(buckets: Sequence[Bucket], start_pos: int, end_pos: int) -> bool:
    series: list[tuple[str, list[float]]] = []
    for family in VFR_FAMILIES:
        values = []
        for bucket in buckets[start_pos : end_pos + 1]:
            stats = bucket.vfr[family]
            if stats.minimum is not None and stats.maximum is not None:
                values.append((stats.minimum + stats.maximum) / 2.0)
        if values:
            series.append(("vfr", values))
    rssi_names = sorted({name for bucket in buckets[start_pos : end_pos + 1] for name in bucket.rssi})
    for name in rssi_names:
        values = []
        for bucket in buckets[start_pos : end_pos + 1]:
            stats = bucket.rssi.get(name)
            if stats is not None and stats.minimum is not None and stats.maximum is not None:
                values.append((stats.minimum + stats.maximum) / 2.0)
        if values:
            series.append(("rssi", values))

    for kind, values in series:
        if len(values) < 20:
            continue
        low = percentile(values, 0.10)
        high = percentile(values, 0.90)
        if low is None or high is None or high - low < MIN_RECEPTION_ROBUST_SPAN:
            continue
        threshold = VFR_SWING_THRESHOLD if kind == "vfr" else RSSI_SWING_THRESHOLD
        if count_directional_swings(values, threshold) >= MIN_RECEPTION_SWINGS:
            return True
    return False


def control_activity(buckets: Sequence[Bucket], start_pos: int, end_pos: int) -> bool:
    active_axes = 0
    total_events = 0
    for name in ("rudder", "elevator", "aileron"):
        values: list[float] = []
        for bucket in buckets[start_pos : end_pos + 1]:
            stats = bucket.controls[name]
            if stats.minimum is not None and stats.maximum is not None:
                values.append((stats.minimum + stats.maximum) / 2.0)
        if len(values) < 2:
            continue
        if max(values) - min(values) >= 100.0:
            active_axes += 1
        total_events += sum(abs(current - previous) >= 30.0 for previous, current in zip(values, values[1:]))
    return active_axes >= 2 and total_events >= 10


def propulsion_activity(buckets: Sequence[Bucket], start_pos: int, end_pos: int) -> bool:
    segment = buckets[start_pos : end_pos + 1]
    throttle_active = sum(bucket.throttle_active for bucket in segment)
    throttle_samples = sum(bucket.throttle_samples for bucket in segment)
    throttle_ratio = throttle_active / throttle_samples if throttle_samples else 0.0
    rpm_seconds = sum(value >= 1200.0 for bucket in segment
                      if (value := bucket.evidence.get("rpm", RangeStats()).maximum) is not None)
    current = evidence_series(buckets, start_pos, end_pos, "propulsion_current_a")
    consumption = evidence_series(buckets, start_pos, end_pos, "propulsion_consumption_mah")
    current_dynamic = bool(current) and max(current) - min(current) >= 2.0
    consumption_rise = len(consumption) >= 2 and consumption[-1] - consumption[0] >= 20.0
    return throttle_ratio >= 0.20 and (rpm_seconds >= 30 or current_dynamic or consumption_rise)


def vertical_activity(buckets: Sequence[Bucket], start_pos: int, end_pos: int) -> bool:
    values = evidence_series(buckets, start_pos, end_pos, "vertical_speed_mps")
    return sum(value >= 1.0 for value in values) >= 5 and sum(value <= -1.0 for value in values) >= 5


def acceleration_activity(buckets: Sequence[Bucket], start_pos: int, end_pos: int) -> bool:
    spans = [
        robust_span(evidence_series(buckets, start_pos, end_pos, family))
        for family in ("accel_x", "accel_y", "accel_z")
    ]
    return sum(span is not None and span >= 0.30 for span in spans) >= 2


def classify_airborne_candidate(
    buckets: Sequence[Bucket], start_pos: int, end_pos: int
) -> tuple[str, str]:
    duration = (buckets[end_pos].last_time - buckets[start_pos].first_time).total_seconds()
    altitude = evidence_series(buckets, start_pos, end_pos, "altitude_m")
    altitude_span = robust_span(altitude)
    airspeed = evidence_series(buckets, start_pos, end_pos, "air_speed_mph")
    gps_speed = evidence_series(buckets, start_pos, end_pos, "gps_speed_mph")
    speed_seconds = max(
        sum(value >= MIN_DIRECT_SPEED_MPH for value in airspeed),
        sum(value >= MIN_DIRECT_SPEED_MPH for value in gps_speed),
    )
    altitude_usable = len(altitude) >= 10
    speed_usable = len(airspeed) >= 10 or len(gps_speed) >= 10

    if altitude_usable and altitude_span is not None:
        if altitude_span >= STRONG_DIRECT_ALTITUDE_SPAN_M:
            return "confirmed", f"altitude span {altitude_span:.1f}m"
        if altitude_span >= MIN_DIRECT_ALTITUDE_SPAN_M and speed_seconds >= MIN_DIRECT_SPEED_SECONDS:
            return "confirmed", f"altitude span {altitude_span:.1f}m; speed {speed_seconds}s"
        if altitude_span < MAX_GROUND_ALTITUDE_SPAN_M and (not speed_usable or speed_seconds < MIN_DIRECT_SPEED_SECONDS):
            return "ground", f"flat altitude {altitude_span:.1f}m; no sustained flight speed"
        if altitude_span < MAX_GROUND_ALTITUDE_SPAN_M and speed_usable:
            return "ground", f"flat altitude {altitude_span:.1f}m"
    elif speed_usable:
        if speed_seconds >= MIN_SPEED_ONLY_SECONDS:
            return "confirmed", f"flight speed {speed_seconds}s"
        if speed_seconds < MIN_DIRECT_SPEED_SECONDS:
            return "ground", "no sustained flight speed"

    components = {
        "controls": control_activity(buckets, start_pos, end_pos),
        "reception": reception_dynamics(buckets, start_pos, end_pos),
        "propulsion": propulsion_activity(buckets, start_pos, end_pos),
        "vertical": vertical_activity(buckets, start_pos, end_pos),
        "acceleration": acceleration_activity(buckets, start_pos, end_pos),
    }
    present = [name for name, active in components.items() if active]
    if duration >= MIN_INDIRECT_FLIGHT_SECONDS and components["controls"] and components["reception"]:
        return "confirmed", "indirect " + ", ".join(present)
    if duration >= MIN_INDIRECT_FLIGHT_SECONDS:
        return "uncertain", "indirect " + (", ".join(present) if present else "candidate only")
    return "rejected", f"only {duration:.0f}s without direct airborne evidence"


def summarize_flight(
    plane: str,
    source: Path,
    buckets: Sequence[Bucket],
    start_pos: int,
    end_pos: int,
    *,
    confidence: str = "confirmed",
    evidence: str = "",
) -> Flight:
    vfr = {family: RangeStats() for family in VFR_FAMILIES}
    temperatures: dict[str, RangeStats] = {}
    sensor_maxima: dict[str, RangeStats] = {}
    max_gap = 0.0
    previous: Bucket | None = None
    for bucket in buckets[start_pos : end_pos + 1]:
        for family in VFR_FAMILIES:
            vfr[family].merge(bucket.output_vfr[family])
        for label, stats in bucket.temperatures.items():
            temperatures.setdefault(label, RangeStats()).merge(stats)
        for label, stats in bucket.sensor_maxima.items():
            sensor_maxima.setdefault(label, RangeStats()).merge(stats)
        if previous is not None:
            max_gap = max(max_gap, (bucket.first_time - previous.last_time).total_seconds())
        previous = bucket

    notes: list[str] = []
    labels = {"rx": "Rx", "2.4ghz": "2.4GHz", "lora": "LoRa", "900mhz": "900MHz"}
    for family in VFR_FAMILIES:
        stats = vfr[family]
        if stats.low_count >= 2:
            notes.append(f"{labels[family]} VFR dropout")
    if max_gap >= 5.0:
        notes.append(f"Telemetry gap {max_gap:.1f}s")
    if confidence == "uncertain":
        notes.insert(0, "Uncertain flight")
    return Flight(
        plane=plane,
        source=source,
        start=buckets[start_pos].first_time,
        end=buckets[end_pos].last_time,
        vfr_minimums={family: vfr[family].minimum for family in VFR_FAMILIES},
        notes="; ".join(notes),
        temperature_maximums={
            label: stats.maximum
            for label, stats in temperatures.items()
            if stats.maximum is not None
        },
        confidence=confidence,
        evidence=evidence,
        sensor_maximums={label: stats.maximum for label, stats in sensor_maxima.items() if stats.maximum is not None},
    )


def analyze_file(
    path: Path,
    *,
    plane_override: str | None,
    scan_root: Path | None,
    bucket_seconds: int,
    rolling_seconds: int,
    merge_gap_seconds: int,
    min_flight_seconds: int,
    max_sensors: Sequence[str] = (),
    exclude_sensors: Sequence[str] = (),
    auto_vfr: bool = True,
    auto_temperatures: bool = True,
    auto_servo_currents: bool = True,
) -> FileAnalysis:
    plane = plane_override or infer_plane(path, scan_root)
    try:
        _headers, buckets, error, complete = load_buckets(path, bucket_seconds, max_sensors,
                                              exclude_sensors=exclude_sensors, auto_vfr=auto_vfr,
                                              auto_temperatures=auto_temperatures,
                                              auto_servo_currents=auto_servo_currents)
    except (OSError, csv.Error, UnicodeError) as exc:
        return FileAnalysis(path, plane, [], "error", str(exc))
    if error:
        return FileAnalysis(path, plane, [], "not-flight", error)
    segments = find_segments(
        buckets,
        bucket_seconds=bucket_seconds,
        rolling_seconds=rolling_seconds,
        merge_gap_seconds=merge_gap_seconds,
        min_flight_seconds=min_flight_seconds,
    )
    flights: list[Flight] = []
    rejected: list[dict[str, str]] = []
    for start, end in segments:
        classification, evidence = classify_airborne_candidate(buckets, start, end)
        if classification in {"confirmed", "uncertain"}:
            flights.append(
                summarize_flight(
                    plane,
                    path,
                    buckets,
                    start,
                    end,
                    confidence=classification,
                    evidence=evidence,
                )
            )
        else:
            rejected.append(
                {
                    "start": buckets[start].first_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "duration": Flight(
                        plane,
                        path,
                        buckets[start].first_time,
                        buckets[end].last_time,
                        {family: None for family in VFR_FAMILIES},
                        "",
                    ).display_duration,
                    "classification": classification,
                    "evidence": evidence,
                }
            )
    detail = "" if flights or not rejected else f"{len(rejected)} candidate(s) rejected by airborne validation"
    excluded = {normalize_header(name) for name in exclude_sensors}
    present = tuple(family for family, indexes in classify_columns(_headers).vfr.items()
                    if auto_vfr and any(normalize_header(_headers[index]) not in excluded for index in indexes))
    flights = [replace(flight, vfr_present=present,
        temperature_maximums={k: v for k, v in flight.temperature_maximums.items() if normalize_header(k) not in excluded},
        sensor_maximums={k: v for k, v in flight.sensor_maximums.items() if normalize_header(k) not in excluded})
        for flight in flights]
    archive_interval = None
    if not flights and buckets and complete and all(item["classification"] == "ground" for item in rejected):
        evidence = archive_airborne_evidence(buckets)
        if evidence:
            detail = f"Recording retained because telemetry shows {evidence}"
        else:
            archive_interval = buckets[0].first_time, buckets[-1].last_time
    return FileAnalysis(path, plane, flights, "flight" if flights else "not-flight", detail, rejected,
                        archive_interval)


def collect_inputs(paths: Sequence[Path]) -> tuple[list[Path], Path | None]:
    files: dict[str, Path] = {}
    roots = [path.resolve() for path in paths if path.is_dir()]
    for path in paths:
        resolved = path.resolve()
        if resolved.is_file() and resolved.suffix.lower() in SUPPORTED_EXTENSIONS:
            files[str(resolved).casefold()] = resolved
        elif resolved.is_dir():
            for candidate in resolved.rglob("*"):
                if candidate.is_file() and candidate.suffix.lower() in SUPPORTED_EXTENSIONS:
                    files[str(candidate.resolve()).casefold()] = candidate.resolve()
        elif not resolved.exists():
            raise FileNotFoundError(f"Input does not exist: {path}")
    scan_root = roots[0] if len(roots) == 1 else None
    return sorted(files.values(), key=lambda path: str(path).casefold()), scan_root


def slugify_plane(plane: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", plane.strip()).strip("-.")
    return slug or "Unsorted"


def split_markdown_row(line: str) -> list[str]:
    placeholder = "\0PIPE\0"
    protected = line.replace("\\|", placeholder)
    cells = [cell.strip().replace(placeholder, "|") for cell in protected.strip().strip("|").split("|")]
    return cells


def format_metric(value: float | None) -> str:
    if value is None:
        return ""
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.1f}"


def escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


@dataclass(frozen=True)
class TableSchema:
    vfr_families: tuple[str, ...]
    maximum_sensors: tuple[str, ...]
    headers: tuple[str, ...] = ()


def vfr_columns(flights: Sequence[Flight]) -> list[str]:
    present = set()
    for flight in flights:
        present.update(flight.vfr_present if flight.vfr_present is not None else
                       (k for k, v in flight.vfr_minimums.items() if v is not None))
    return [family for family in VFR_FAMILIES if family in present]


def table_header(temperature_sensors: Sequence[str], vfr_families: Sequence[str] = VFR_FAMILIES) -> str:
    columns = ["Flight number", "Date/time", "Flight duration"]
    columns.extend(VFR_LABELS[family] for family in vfr_families)
    columns.extend(f"Maximum {escape_cell(sensor)}" for sensor in temperature_sensors)
    columns.append("Notes")
    return "| " + " | ".join(columns) + " |"


def table_separator(temperature_sensors: Sequence[str], vfr_families: Sequence[str] = VFR_FAMILIES) -> str:
    alignments = ["---:", "---", "---:"] + ["---:"] * (len(vfr_families) + len(temperature_sensors)) + ["---"]
    return "|" + "|".join(alignments) + "|"


def schema_from_header(line: str) -> TableSchema | None:
    if not line.strip().startswith("|"):
        return None
    cells = split_markdown_row(line)
    required = {"Flight number", "Date/time", "Flight duration", "Notes"}
    if len(cells) < 4 or not required.issubset(cells) or len(set(cells)) != len(cells):
        return None
    labels = {value: key for key, value in VFR_LABELS.items()}
    labels["Minimum RxVFR"] = "rx"
    families, sensors = [], []
    for cell in cells:
        if cell in required:
            continue
        if cell in labels:
            families.append(labels[cell])
        elif cell.startswith("Maximum ") and cell[8:].strip():
            sensors.append(cell[8:].strip())
        else:
            return None
    if len(set(families)) != len(families) or len({normalize_header(s) for s in sensors}) != len(sensors):
        return None
    return TableSchema(tuple(families), tuple(sensors), tuple(cells))


def parse_existing_log(path: Path) -> tuple[str, list[ExistingRow], int, TableSchema]:
    with path.open(encoding="utf-8", newline="") as handle:
        text = handle.read()
    lines = text.splitlines(keepends=True)
    header_position = None
    schema = None
    for index, line in enumerate(lines):
        schema = schema_from_header(line.rstrip("\r\n"))
        if schema is not None:
            header_position = index
            break
    if schema is None or header_position is None or header_position + 1 >= len(lines):
        raise ValueError(f"{path.name} does not contain the expected flight-log table header")
    expected_cells = 4 + len(schema.vfr_families) + len(schema.maximum_sensors)
    separator = split_markdown_row(lines[header_position + 1])
    if len(separator) != expected_cells or not all(re.fullmatch(r":?-{3,}:?", c) for c in separator):
        raise ValueError(f"{path.name} has an invalid table separator")
    rows = []
    insertion_line = header_position + 2
    for index in range(insertion_line, len(lines)):
        stripped = lines[index].strip()
        if not stripped.startswith("|"):
            break
        cells = split_markdown_row(stripped)
        if len(cells) != expected_cells:
            raise ValueError(f"Cannot safely parse existing row {index + 1} in {path.name}: wrong cell count")
        try:
            number = int(cells[schema.headers.index("Flight number")])
            timestamp = datetime.strptime(cells[schema.headers.index("Date/time")], "%Y-%m-%d %H:%M:%S")
            duration = cells[schema.headers.index("Flight duration")]
            if not re.fullmatch(r"\d{2,}:\d{2}:\d{2}", duration):
                raise ValueError("invalid duration")
        except ValueError as exc:
            raise ValueError(f"Cannot safely parse existing row {index + 1} in {path.name}: {exc}") from exc
        rows.append(ExistingRow(number, timestamp, duration, cells[schema.headers.index("Notes")]))
        insertion_line = index + 1
    if any(rows[i].timestamp > rows[i + 1].timestamp for i in range(len(rows) - 1)):
        raise ValueError(f"{path.name} is not ordered by date/time ascending")
    offset = sum(len(line) for line in lines[:insertion_line])
    return text, rows, offset, schema


def temperature_columns(flights: Sequence[Flight]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for flight in flights:
        for sensor in flight.temperature_maximums:
            key = normalize_header(sensor)
            if key not in seen:
                seen.add(key)
                result.append(sensor)
    return result


def maximum_columns(flights: Sequence[Flight]) -> list[str]:
    result = temperature_columns(flights)
    seen = {normalize_header(sensor) for sensor in result}
    for flight in flights:
        for sensor in flight.sensor_maximums:
            if normalize_header(sensor) not in seen:
                seen.add(normalize_header(sensor))
                result.append(sensor)
    return result


def temperature_value(flight: Flight, sensor: str) -> float | None:
    target = normalize_header(sensor)
    return next((value for label, value in {**flight.temperature_maximums, **flight.sensor_maximums}.items()
                 if normalize_header(label) == target), None)


def flight_row(number: int, flight: Flight, temperature_sensors: Sequence[str] = (),
               vfr_families: Sequence[str] = VFR_FAMILIES,
               headers: Sequence[str] = ()) -> str:
    values = [str(number), flight.display_datetime, flight.display_duration]
    values.extend(format_metric(flight.vfr_minimums.get(family)) for family in vfr_families)
    values.extend(format_metric(temperature_value(flight, sensor)) for sensor in temperature_sensors)
    values.append(escape_cell(flight.notes))
    if headers:
        canonical = split_markdown_row(table_header(temperature_sensors, vfr_families))
        by_header = dict(zip(canonical, values))
        by_header["Minimum RxVFR"] = format_metric(flight.vfr_minimums.get("rx"))
        values = [by_header[header] for header in headers]
    return "| " + " | ".join(values) + " |"


def new_log_text(plane: str, flights: Sequence[Flight], temperature_sensors: Sequence[str] | None = None) -> str:
    sensors = list(temperature_sensors) if temperature_sensors is not None else maximum_columns(flights)
    families = vfr_columns(flights)
    lines = [f"# {plane} Flight Log", "", table_header(sensors, families), table_separator(sensors, families)]
    lines.extend(flight_row(index, flight, sensors, families) for index, flight in enumerate(flights, start=1))
    return "\n".join(lines) + "\n"

