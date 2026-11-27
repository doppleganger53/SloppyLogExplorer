"""Bin one recording's paired reception samples on both HeatMap grids."""

from __future__ import annotations

import re

import pandas as pd

from .models import LoadedLog
from .reception import (
    _row_median,
    heatmap_gps_rows,
    logical_telemetry_groups,
    reception_cell_key,
)


_RECEPTION_CHANNEL = re.compile(
    r"\b(?:(?:rx|tx)?(?:vfr|rssi\d*)|[12]rss|[rt](?:qly|snr|rss)|link quality|signal strength|lq|snr"
    r"|spektrum (?:[ablr] fades|frame losses|holds))\b",
    re.IGNORECASE,
)


def bin_history_recording(
    log: LoadedLog, site_center: tuple[float, float],
    *, grid_origin: tuple[float, float] | None = None,
) -> dict[str, dict[str, list[list[float | int]]]]:
    """Return [east index, north index, median, sample count] per observed cell."""
    grids: dict[str, dict[str, list[list[float | int]]]] = {"5": {}, "2": {}}
    numeric = set(log.numeric_columns)
    channels = {
        name: columns for name, columns in logical_telemetry_groups(log.dataframe.columns).items()
        if _RECEPTION_CHANNEL.search(name) and numeric.intersection(columns)
    }
    if not channels:
        return grids

    fixes, _ = heatmap_gps_rows(log, site_center)
    if not fixes:
        return grids

    rows = [row for row, _, _ in fixes]
    values = pd.DataFrame({
        name: _row_median(log.dataframe, columns).iloc[rows].to_numpy()
        for name, columns in channels.items()
    })
    origin = grid_origin if grid_origin is not None else site_center
    for cell_size in (5, 2):
        keys = [reception_cell_key(lat, lon, origin, cell_size) for _, lat, lon in fixes]
        values.index = pd.MultiIndex.from_tuples(keys)
        grouped = values.groupby(level=[0, 1])
        medians = grouped.median()
        counts = grouped.count()
        for name in channels:
            observed = [
                [int(x), int(y), float(value), int(count)]
                for ((x, y), value), count in zip(medians[name].items(), counts[name])
                if count
            ]
            if observed:
                grids[str(cell_size)][name] = observed
    return grids
