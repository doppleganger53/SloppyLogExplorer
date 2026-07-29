"""Shared telemetry, GPS, and sync data containers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

RECEPTION_INDEX_VERSION = 3


@dataclass(frozen=True)
class GpsColumns:
    latitude: str
    longitude: str
    altitude: str | None = None
    latitude_label: str | None = None
    longitude_label: str | None = None
    altitude_label: str | None = None


@dataclass(frozen=True)
class GpsGradientOptions:
    color_column: str | None = None
    start_color: str = "#2f80ed"
    end_color: str = "#eb5757"
    reverse: bool = False
    auto_range: bool = True
    range_min: float | None = None
    range_max: float | None = None
    midpoint: float | None = None
    scope_start_seconds: float | None = None
    scope_end_seconds: float | None = None


@dataclass(frozen=True)
class LibraryLogInfo:
    path: Path
    model: str
    name: str
    modified: float
    size: int


@dataclass(frozen=True)
class ReceptionLogRecord:
    library_root: Path
    file_path: Path
    file_size: int
    mtime_ns: int
    status: str
    flight_date: date
    date_inferred: bool
    center_latitude: float | None
    center_longitude: float | None
    channels: tuple[str, ...] = ()
    error: str = ""
    site_id: int | None = None
    index_version: int = RECEPTION_INDEX_VERSION

    @property
    def path(self) -> Path:
        return self.file_path

    @property
    def size(self) -> int:
        return self.file_size

    @property
    def modified_ns(self) -> int:
        return self.mtime_ns

    @property
    def date_is_inferred(self) -> bool:
        return self.date_inferred

    @property
    def centroid_latitude(self) -> float | None:
        return self.center_latitude

    @property
    def centroid_longitude(self) -> float | None:
        return self.center_longitude

    @property
    def numeric_channels(self) -> tuple[str, ...]:
        return self.channels


@dataclass(frozen=True)
class DetectedSiteCluster:
    center_latitude: float
    center_longitude: float
    records: tuple[ReceptionLogRecord, ...] = ()
    file_paths: tuple[str | Path, ...] = ()

    def __post_init__(self) -> None:
        if not self.file_paths:
            object.__setattr__(self, "file_paths", tuple(record.file_path for record in self.records))


@dataclass(frozen=True)
class FlyingSite:
    id: int
    library_root: Path
    name: str
    notes: str
    active: bool
    center_latitude: float
    center_longitude: float
    log_count: int = 0


@dataclass(frozen=True)
class ReceptionCell:
    latitude: float
    longitude: float
    polygon: tuple[tuple[float, float], ...]
    value: float
    sample_count: int
    flight_count: int


@dataclass(frozen=True)
class TelemetryChannelCoverage:
    channel: str
    count: int
    total: int


@dataclass(frozen=True)
class ReceptionScanResult:
    records: tuple[ReceptionLogRecord, ...]
    clusters: tuple[DetectedSiteCluster, ...]
    scanned_count: int
    cached_count: int
    error_count: int
    cancelled: bool = False


@dataclass(frozen=True)
class LogFileInfo:
    path: Path
    model: str
    name: str
    rows: int
    columns: int
    modified: float
    start_time: pd.Timestamp | None
    end_time: pd.Timestamp | None
    duration_seconds: float
    has_gps: bool


@dataclass
class LoadedLog:
    info: LogFileInfo
    dataframe: pd.DataFrame
    time: pd.Series | None
    numeric_columns: list[str]
    gps_columns: GpsColumns | None
    timeline_columns: frozenset[str] = field(default_factory=frozenset)

    @property
    def parameter_columns(self) -> list[str]:
        hidden = set()
        if self.time is not None:
            # Keep timeline fields out of the plot picker so users only see
            # actual telemetry parameters instead of redundant time columns.
            hidden.update(self.timeline_columns)
        # Internal helper columns are injected during GPS parsing and should
        # never surface as normal plot candidates.
        hidden.update(c for c in self.dataframe.columns if c.startswith("__"))
        return [c for c in self.numeric_columns if c not in hidden]


@dataclass(frozen=True)
class SyncCandidate:
    source: Path
    target: Path
    relative_path: Path
    reason: str
    source_mtime: float
    target_mtime: float | None


@dataclass(frozen=True)
class InternalResistanceResult:
    pack_milliohm: float
    cell_milliohm: float
    cells: int
    health: str
    samples: int
    voltage_column: str
    current_column: str
    current_range: tuple[float, float]
    voltage_range: tuple[float, float]


@dataclass(frozen=True)
class CursorValue:
    column: str
    value: Any
    compare_value: Any | None
    delta: Any | None
