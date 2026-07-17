"""Shared telemetry, GPS, and sync data containers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd


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
