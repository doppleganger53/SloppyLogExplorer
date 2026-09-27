"""Column heuristics and simple telemetry analysis helpers."""

from __future__ import annotations

import math
from typing import Iterable, cast

import numpy as np
import pandas as pd

from .models import CursorTimeline, CursorValue, InternalResistanceResult, LoadedLog


def _numeric_series(df: pd.DataFrame, column: str) -> pd.Series:
    series = df[column]
    if isinstance(series, pd.DataFrame):
        series = pd.Series(series.to_numpy()[:, 0], index=series.index)
    numeric = pd.to_numeric(series, errors="coerce")
    return pd.Series(numeric, index=series.index).where(np.isfinite(numeric))


# Keep the most useful telemetry channels near the top of the UI so the first
# plot selection usually produces a meaningful graph without manual curation.
PREFERRED_DISPLAY_COLUMNS = [
    "VFAS(V)",
    "TRUE Current(A)",
    "Current(A)",
    "RxBatt(V)",
    "BEC voltage(V)",
    "VFR 2.4G(%)",
    "Rx VFR(%)",
    "VFR 900M(%)",
    "RSSI 2.4G(dB)",
    "RSSI 900M(dB)",
    "Altitude(m)",
]


def _column_priority(column: str, patterns: list[tuple[str, int]], default: int = 100) -> int:
    name = column.lower()
    for pattern, score in patterns:
        if pattern in name:
            return score
    return default


def suggest_display_columns(columns: Iterable[str], limit: int = 5) -> list[str]:
    available = list(columns)
    selected: list[str] = []
    for preferred in PREFERRED_DISPLAY_COLUMNS:
        if preferred in available and preferred not in selected:
            selected.append(preferred)
    for column in available:
        if len(selected) >= limit:
            break
        if column not in selected:
            selected.append(column)
    return selected[:limit]


def find_voltage_columns(columns: Iterable[str]) -> list[str]:
    excluded = ("current", "curr", "consum", "mah", "(a)", "temp", "status")
    voltage_columns = []
    for column in columns:
        name = column.lower()
        if any(token in name for token in excluded):
            continue
        if any(token in name for token in ("(v)", "volt", "vfas", "rxbat", "rxbatt", "bat1", "bat2", "adc")):
            voltage_columns.append(column)

    # Score the common pack-voltage spellings ahead of more ambiguous voltage-
    # related channels such as BEC or ADC readings.
    priorities = [
        ("vfas", 0),
        ("main voltage", 1),
        ("esc voltage", 2),
        ("bat1 voltage", 3),
        ("bat2 voltage", 4),
        ("1 cell", 5),
        ("rxbat", 20),
        ("rxbatt", 20),
        ("bec voltage", 25),
        ("txbat", 50),
        ("adc", 60),
        ("srv", 80),
    ]
    return sorted(voltage_columns, key=lambda column: (_column_priority(column, priorities), column.lower()))


def find_current_columns(columns: Iterable[str]) -> list[str]:
    current_columns = []
    for column in columns:
        name = column.lower()
        if any(token in name for token in ("current", "curr", "(a)")) and "consum" not in name:
            current_columns.append(column)

    def priority(column: str) -> tuple[int, str]:
        name = column.lower().strip()
        # Prefer true pack current channels before ESC, BEC, or servo currents
        # because those are the values most likely to describe battery load.
        if "true current" in name:
            score = 0
        elif name in {"current(a)", "current (a)", "current"}:
            score = 1
        elif "bec current" in name:
            score = 20
        elif "srv" in name:
            score = 40
        elif "curr" in name:
            score = 50
        else:
            score = 60
        return score, name

    return sorted(current_columns, key=priority)


def guess_cell_count(voltage: pd.Series) -> int:
    voltage = voltage.where(np.isfinite(voltage))
    median = float(voltage.dropna().median()) if voltage.notna().any() else 0.0
    if median <= 0:
        return 1
    # Divide by roughly one LiPo cell's nominal voltage to get a practical
    # pack-size guess when the log does not provide an explicit cell count.
    return max(1, min(14, int(round(median / 3.8))))


def health_label(cell_milliohm: float) -> str:
    if cell_milliohm <= 5:
        return "Excellent"
    if cell_milliohm <= 10:
        return "Good"
    if cell_milliohm <= 18:
        return "Fair"
    if cell_milliohm <= 30:
        return "Poor"
    return "Replace"


def calculate_internal_resistance(
    df: pd.DataFrame,
    voltage_column: str,
    current_column: str,
    cells: int | None = None,
) -> InternalResistanceResult | None:
    voltage = _numeric_series(df, voltage_column)
    current = _numeric_series(df, current_column)
    data = pd.DataFrame({"voltage": voltage, "current": current}).dropna()
    data = cast(pd.DataFrame, data[data["current"].abs() > 0.5])
    if len(data) < 8 or data["current"].max() - data["current"].min() < 1.0:
        return None

    current_values = _numeric_series(data, "current")
    voltage_values = _numeric_series(data, "voltage")

    # Voltage drop versus current should slope downward; the negative sign
    # converts that fitted slope into a positive milliohm estimate.
    slope, _intercept = np.polyfit(np.asarray(current_values, dtype=float), np.asarray(voltage_values, dtype=float), 1)
    pack_milliohm = max(0.0, -float(slope) * 1000.0)
    cell_count = cells or guess_cell_count(voltage_values)
    cell_milliohm = pack_milliohm / max(1, cell_count)
    if not math.isfinite(pack_milliohm) or pack_milliohm <= 0:
        return None

    return InternalResistanceResult(
        pack_milliohm=pack_milliohm,
        cell_milliohm=cell_milliohm,
        cells=cell_count,
        health=health_label(cell_milliohm),
        samples=len(data),
        voltage_column=voltage_column,
        current_column=current_column,
        current_range=(float(current_values.min()), float(current_values.max())),
        voltage_range=(float(voltage_values.min()), float(voltage_values.max())),
    )


def _cursor_timeline(log: LoadedLog) -> CursorTimeline | None:
    if log.cursor_timeline is not None and log.cursor_timeline.source is log.time:
        return log.cursor_timeline
    if log.time is None or not log.time.notna().any():
        return None
    clean = log.time.ffill().bfill()
    absolute = (clean - pd.Timestamp("1970-01-01")).dt.total_seconds().to_numpy(dtype=float)
    elapsed = (clean - clean.iloc[0]).dt.total_seconds().clip(lower=0).to_numpy(dtype=float)
    log.cursor_timeline = CursorTimeline(
        source=log.time,
        absolute_seconds=absolute,
        elapsed_seconds=elapsed,
        monotonic=bool(np.all(absolute[1:] >= absolute[:-1])),
        absolute_bounds=(float(absolute.min()), float(absolute.max())),
        elapsed_bounds=(float(elapsed.min()), float(elapsed.max())),
    )
    return log.cursor_timeline


def _nearest_compare_index(timeline: CursorTimeline, target: float, absolute: bool) -> int | None:
    values = timeline.absolute_seconds if absolute else timeline.elapsed_seconds
    lower, upper = timeline.absolute_bounds if absolute else timeline.elapsed_bounds
    if not lower <= target <= upper:
        return None
    if not timeline.monotonic:
        return int(np.abs(values - target).argmin())
    insertion = int(np.searchsorted(values, target, side="left"))
    if insertion == 0:
        return 0
    if insertion == len(values):
        return len(values) - 1
    if values[insertion] - target < target - values[insertion - 1]:
        return insertion
    return insertion - 1


def cursor_values(
    primary: LoadedLog,
    index: int,
    columns: list[str],
    compare: LoadedLog | None = None,
    *,
    time_mode: str = "absolute",
) -> list[CursorValue]:
    if primary.dataframe.empty:
        return []
    index = max(0, min(index, len(primary.dataframe) - 1))
    compare_index = min(index, len(compare.dataframe) - 1) if compare is not None and not compare.dataframe.empty else None
    if compare is not None and compare_index is not None:
        primary_timeline, compare_timeline = _cursor_timeline(primary), _cursor_timeline(compare)
        if primary_timeline is not None and compare_timeline is not None:
            absolute = time_mode == "absolute"
            target = primary_timeline.absolute_seconds[index] if absolute else primary_timeline.elapsed_seconds[index]
            compare_index = _nearest_compare_index(compare_timeline, float(target), absolute)
    values: list[CursorValue] = []
    primary_row = primary.dataframe.iloc[index]
    compare_row = compare.dataframe.iloc[compare_index] if compare is not None and compare_index is not None else None
    for col in columns:
        if col not in primary.dataframe.columns:
            continue
        value = primary_row[col]
        compare_value = None
        delta = None
        if compare_row is not None and col in compare_row.index:
            compare_value = compare_row[col]
            try:
                delta = float(value) - float(compare_value)
                if not math.isfinite(delta):
                    delta = None
            except (TypeError, ValueError, OverflowError):
                delta = None
        values.append(CursorValue(column=col, value=value, compare_value=compare_value, delta=delta))
    return values


def basic_stats(df: pd.DataFrame, columns: list[str]) -> dict[str, dict[str, float | int]]:
    stats: dict[str, dict[str, float | int]] = {}
    for col in columns:
        if col not in df.columns:
            continue
        series = _numeric_series(df, col).dropna()
        if series.empty:
            continue
        stats[col] = {
            "count": int(len(series)),
            "min": float(series.min()),
            "max": float(series.max()),
            "mean": float(series.mean()),
            "std": float(series.std()) if len(series) > 1 else 0.0,
        }
    return stats
