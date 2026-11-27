"""Bin paired reception samples and frame the site's observed flying area."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

import numpy as np
import pandas as pd

from .models import LoadedLog
from .reception import (
    _row_median,
    heatmap_gps_rows,
    logical_telemetry_groups,
    reception_cell_key,
    reception_viewport_neighborhood,
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


def history_site_bounds(logs: Iterable[dict]) -> list[float] | None:
    """Return camera bounds in grid-local metres, leaving every observed cell in the history."""
    samples: Counter[tuple[int, int]] = Counter()
    recordings: Counter[tuple[int, int]] = Counter()
    for log in logs:
        # A GPS update can be paired with several reception channels. Count a
        # visited cell once per recording, regardless of how many radios it has.
        per_recording: dict[tuple[int, int], int] = {}
        for channel in log["grids"]["5"].values():
            for x, y, _value, count in channel:
                key = (x, y)
                per_recording[key] = max(per_recording.get(key, 0), count)
        samples.update(per_recording)
        recordings.update(per_recording.keys())

    if not samples:
        return None
    cell_size = 5.0
    points = [(x * cell_size, y * cell_size, count, recordings[x, y]) for (x, y), count in samples.items()]
    if len(points) > 2:
        neighborhood = reception_viewport_neighborhood(points)
        assert neighborhood is not None  # There are observed cells with finite grid indices.
        points = neighborhood.cells

    # The sampled neighborhood locates the field. Its footprint comes from
    # distinct cells so hours parked in one cell cannot shrink the camera.
    coords = np.array([(x, y) for x, y, _, _ in points])
    median_x, median_y = np.median(coords, axis=0)
    radial = np.hypot(coords[:, 0] - median_x, coords[:, 1] - median_y)
    q1, q3 = np.percentile(radial, (25, 75))
    outer_limit = q3 + 1.5 * (q3 - q1) + np.hypot(cell_size, cell_size)
    coords = coords[radial <= outer_limit]
    low, high = coords.min(axis=0), coords.max(axis=0)
    return [float(low[0]), float(high[0]), float(low[1]), float(high[1])]
