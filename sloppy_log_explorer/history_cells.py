"""Bin one recording's paired reception samples on both HeatMap grids."""

from __future__ import annotations

import re

import pandas as pd

from .models import LoadedLog
from .parser import relative_seconds
from .reception import (
    HEATMAP_SITE_RADIUS_KM,
    _haversine_km,
    _row_median,
    _valid_gps_candidates,
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
) -> dict[str, dict[str, list[list[float | int]]]]:
    """Return [east index, north index, median, sample count] per observed cell."""
    grids: dict[str, dict[str, list[list[float | int]]]] = {"5": {}, "2": {}}
    gps = log.gps_columns
    numeric = set(log.numeric_columns)
    channels = {
        name: columns for name, columns in logical_telemetry_groups(log.dataframe.columns).items()
        if _RECEPTION_CHANNEL.search(name) and numeric.intersection(columns)
    }
    if gps is None or not channels:
        return grids

    candidates = _valid_gps_candidates(
        log.dataframe, gps.latitude, gps.longitude,
        time_values=log.time.to_list() if log.time is not None else None,
        elapsed_values=relative_seconds(log),
    )
    rows = []
    coordinates = []
    for point, _time in candidates:
        lat, lon = float(point["lat"]), float(point["lon"])
        if _haversine_km(*site_center, lat, lon) <= HEATMAP_SITE_RADIUS_KM:
            rows.append(int(point["row"]) - 1)
            coordinates.append((lat, lon))
    if not rows:
        return grids

    values = pd.DataFrame({
        name: _row_median(log.dataframe, columns).iloc[rows].to_numpy()
        for name, columns in channels.items()
    })
    for cell_size in (5, 2):
        keys = [reception_cell_key(lat, lon, site_center, cell_size) for lat, lon in coordinates]
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
