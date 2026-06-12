"""Column heuristics and simple telemetry analysis helpers."""

from __future__ import annotations

import math
from typing import Iterable, cast

import numpy as np
import pandas as pd

from .models import CursorValue, InternalResistanceResult, LoadedLog


def _numeric_series(df: pd.DataFrame, column: str) -> pd.Series:
    series = df[column]
    if isinstance(series, pd.DataFrame):
        series = series.iloc[:, 0]
    numeric = pd.to_numeric(series, errors="coerce")
    return pd.Series(numeric, index=series.index)


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


def cursor_values(
    primary: LoadedLog,
    index: int,
    columns: list[str],
    compare: LoadedLog | None = None,
) -> list[CursorValue]:
    if primary.dataframe.empty:
        return []
    index = max(0, min(index, len(primary.dataframe) - 1))
    # Compare logs can be shorter than the primary log, so clamp the cursor to
    # the last available compare row instead of indexing past the end.
    compare_index = min(index, len(compare.dataframe) - 1) if compare is not None and not compare.dataframe.empty else None
    values: list[CursorValue] = []
    for col in columns:
        if col not in primary.dataframe.columns:
            continue
        value = primary.dataframe.iloc[index][col]
        compare_value = None
        delta = None
        if compare is not None and compare_index is not None and col in compare.dataframe.columns:
            compare_value = compare.dataframe.iloc[compare_index][col]
            try:
                delta = float(value) - float(compare_value)
            except Exception:
                delta = None
        values.append(CursorValue(column=col, value=value, compare_value=compare_value, delta=delta))
    return values


def basic_stats(df: pd.DataFrame, columns: list[str]) -> dict[str, dict[str, float]]:
    stats: dict[str, dict[str, float]] = {}
    for col in columns:
        if col not in df.columns:
            continue
        series = _numeric_series(df, col).dropna()
        if series.empty:
            continue
        stats[col] = {
            "min": float(series.min()),
            "max": float(series.max()),
            "mean": float(series.mean()),
            "std": float(series.std()) if len(series) > 1 else 0.0,
        }
    return stats
