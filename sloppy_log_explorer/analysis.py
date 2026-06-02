from __future__ import annotations

import math
from typing import Iterable

import numpy as np
import pandas as pd

from .models import CursorValue, InternalResistanceResult, LoadedLog


def _numeric_series(df: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(df[column], errors="coerce")


def find_voltage_columns(columns: Iterable[str]) -> list[str]:
    needles = ("volt", "vfas", "rxbat", "rxbatt", "bec", "(v)")
    return [col for col in columns if any(token in col.lower() for token in needles)]


def find_current_columns(columns: Iterable[str]) -> list[str]:
    needles = ("current", "curr", "(a)")
    return [col for col in columns if any(token in col.lower() for token in needles)]


def guess_cell_count(voltage: pd.Series) -> int:
    median = float(voltage.dropna().median()) if voltage.notna().any() else 0.0
    if median <= 0:
        return 1
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
    data = data[data["current"].abs() > 0.5]
    if len(data) < 8 or data["current"].max() - data["current"].min() < 1.0:
        return None

    slope, _intercept = np.polyfit(data["current"].to_numpy(), data["voltage"].to_numpy(), 1)
    pack_milliohm = max(0.0, -float(slope) * 1000.0)
    cell_count = cells or guess_cell_count(data["voltage"])
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
        current_range=(float(data["current"].min()), float(data["current"].max())),
        voltage_range=(float(data["voltage"].min()), float(data["voltage"].max())),
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
        series = pd.to_numeric(df[col], errors="coerce").dropna()
        if series.empty:
            continue
        stats[col] = {
            "min": float(series.min()),
            "max": float(series.max()),
            "mean": float(series.mean()),
            "std": float(series.std()) if len(series) > 1 else 0.0,
        }
    return stats

