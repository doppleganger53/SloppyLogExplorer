"""Plotly chart and GPS map rendering helpers."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
import math
import re
from collections.abc import Sequence
from numbers import Real
from typing import Any, TypedDict

import pandas as pd
import plotly
import plotly.graph_objects as go
from plotly.offline import get_plotlyjs

from .gps_map_renderer import (
    ALTITUDE_EXAGGERATION,
    ALTITUDE_FLOOR_METERS,
    MAP_FIT_MAX_ZOOM,
    MAP_MAX_ZOOM,
    MAP_MAX_PITCH,
    OPENSTREETMAP_RASTER_TILE_MAX_ZOOM,
    OPENSTREETMAP_RASTER_TILE_URL,
    build_gps_map_html as render_gps_map_html,
)
from .models import GpsGradientOptions, LoadedLog
from .parser import relative_seconds
from .storage import app_data_dir

COLORS = [
    "#2f80ed",
    "#f2994a",
    "#27ae60",
    "#eb5757",
    "#9b51e0",
    "#00a3a3",
    "#f2c94c",
    "#56ccf2",
]

DEFAULT_PATH_COLOR = "#55d977"
MISSING_VALUE_COLOR = "#9ca3af"
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
TELEMETRY_UNIT_RE = re.compile(r"\(([^()]+)\)\s*$")
MAX_TELEMETRY_TRACE_POINTS_HARD_CAP = 12000
MIN_TELEMETRY_TRACE_POINTS = 2000
TELEMETRY_SAMPLES_PER_PIXEL = 4
MAX_RENDERED_TELEMETRY_TRACES = 24
MAX_VISIBLE_TELEMETRY_AXES = 6
MAX_VISIBLE_LEGEND_ITEMS = 12
MAX_STATS_ANNOTATION_ROWS = 12
TELEMETRY_MARGIN_LEFT = 58
TELEMETRY_MARGIN_RIGHT_BASE = 64
TELEMETRY_MARGIN_RIGHT_PER_AXIS = 62
TELEMETRY_MARGIN_TOP = 30
TELEMETRY_MARGIN_BOTTOM = 46
TELEMETRY_RIGHT_AXIS_SPACING = 0.055
TELEMETRY_RIGHT_AXIS_DOMAIN_GAP = 0.035
TELEMETRY_RIGHT_AXIS_MIN_DOMAIN_END = 0.55
MAX_GPS_JUMP_KM = 1000.0
# A tiny origin tolerance catches placeholder zeros without rejecting genuine
# coordinates that are only close to zero.
GPS_ORIGIN_EPSILON = 1e-9


@dataclass(frozen=True)
class TelemetryAxisGroup:
    columns: tuple[str, ...]
    title: str
    color_index: int


def _plotly_js_uri() -> str:
    asset_dir = app_data_dir() / "assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    script_path = asset_dir / f"plotly-{plotly.__version__}.min.js"
    if not script_path.exists() or script_path.stat().st_size == 0:
        script_path.write_text(get_plotlyjs(), encoding="utf-8")
    return script_path.as_uri()


def _plotly_script_tag() -> str:
    return f'<script src="{escape(_plotly_js_uri(), quote=True)}"></script>'


def _axis_name(index: int) -> str:
    return "y" if index == 0 else f"y{index + 1}"


def _telemetry_right_margin(plotted_column_count: int) -> int:
    return TELEMETRY_MARGIN_RIGHT_BASE + _telemetry_visible_right_axis_count(
        plotted_column_count
    ) * TELEMETRY_MARGIN_RIGHT_PER_AXIS


def _telemetry_visible_axis_count(plotted_column_count: int) -> int:
    return min(max(0, plotted_column_count), MAX_VISIBLE_TELEMETRY_AXES)


def _telemetry_visible_right_axis_count(plotted_column_count: int) -> int:
    return max(0, _telemetry_visible_axis_count(plotted_column_count) - 1)


def _telemetry_right_axis_positions(plotted_column_count: int) -> list[float]:
    right_axis_count = _telemetry_visible_right_axis_count(plotted_column_count)
    if right_axis_count <= 0:
        return []
    start = 1.0 - (right_axis_count - 1) * TELEMETRY_RIGHT_AXIS_SPACING
    return [round(start + offset * TELEMETRY_RIGHT_AXIS_SPACING, 6) for offset in range(right_axis_count)]


def _telemetry_x_axis_domain(plotted_column_count: int) -> list[float]:
    positions = _telemetry_right_axis_positions(plotted_column_count)
    if len(positions) <= 1:
        return [0.0, 1.0]
    domain_end = max(
        TELEMETRY_RIGHT_AXIS_MIN_DOMAIN_END,
        positions[0] - TELEMETRY_RIGHT_AXIS_DOMAIN_GAP,
    )
    return [0.0, round(domain_end, 6)]


def _telemetry_column_unit(column: str) -> str | None:
    match = TELEMETRY_UNIT_RE.search(column.strip())
    if match is None:
        return None
    unit = match.group(1).strip()
    return unit or None


def _telemetry_range(primary: LoadedLog, column: str) -> tuple[float, float] | None:
    if column not in primary.dataframe.columns:
        return None
    series = _numeric_series(primary.dataframe, column)
    minimum = _coerce_float(series.min())
    maximum = _coerce_float(series.max())
    if minimum is None or maximum is None:
        return None
    if maximum < minimum:
        minimum, maximum = maximum, minimum
    return minimum, maximum


def _telemetry_ranges_are_similar(
    left: tuple[float, float],
    right: tuple[float, float],
) -> bool:
    left_min, left_max = left
    right_min, right_max = right
    left_span = max(0.0, left_max - left_min)
    right_span = max(0.0, right_max - right_min)
    left_magnitude = max(abs(left_min), abs(left_max), 1.0)
    right_magnitude = max(abs(right_min), abs(right_max), 1.0)
    magnitude_ratio = max(left_magnitude, right_magnitude) / min(left_magnitude, right_magnitude)
    if magnitude_ratio > 2.5:
        return False

    overlap = min(left_max, right_max) - max(left_min, right_min)
    if overlap >= 0:
        return True
    gap = -overlap
    tolerance = max(left_span, right_span, left_magnitude * 0.05, right_magnitude * 0.05, 1.0)
    return gap <= tolerance


def _telemetry_group_title(columns: Sequence[str], manual: bool = False) -> str:
    if len(columns) == 1:
        return columns[0]
    units = {_telemetry_column_unit(column) for column in columns}
    units.discard(None)
    if len(units) == 1 and all(_telemetry_column_unit(column) in units for column in columns):
        return f"{next(iter(units))} group"
    return "Grouped axis" if manual else columns[0]


def _normalise_manual_axis_groups(
    plotted_columns: Sequence[str],
    manual_axis_groups: Sequence[Sequence[str]] | None,
) -> list[tuple[str, ...]]:
    plotted = set(plotted_columns)
    claimed: set[str] = set()
    groups: list[tuple[str, ...]] = []
    for group in manual_axis_groups or ():
        columns: list[str] = []
        seen: set[str] = set()
        for column in group:
            if column not in plotted or column in seen or column in claimed:
                continue
            columns.append(column)
            seen.add(column)
        if len(columns) < 2:
            continue
        groups.append(tuple(columns))
        claimed.update(columns)
    return groups


def _resolve_telemetry_axis_groups(
    primary: LoadedLog,
    columns: Sequence[str],
    manual_axis_groups: Sequence[Sequence[str]] | None = None,
    ungrouped_axis_columns: Sequence[str] | None = None,
) -> list[TelemetryAxisGroup]:
    plotted_columns = list(columns[:MAX_RENDERED_TELEMETRY_TRACES])
    column_order = {column: index for index, column in enumerate(plotted_columns)}
    manual_groups = _normalise_manual_axis_groups(plotted_columns, manual_axis_groups)
    manual_columns = {column for group in manual_groups for column in group}
    ungrouped_columns = set(ungrouped_axis_columns or ()) & set(plotted_columns)
    ungrouped_columns -= manual_columns

    groups: list[TelemetryAxisGroup] = []
    consumed = set(manual_columns)
    for group in manual_groups:
        groups.append(
            TelemetryAxisGroup(
                columns=group,
                title=_telemetry_group_title(group, manual=True),
                color_index=column_order[group[0]],
            )
        )

    available = [
        column
        for column in plotted_columns
        if column not in consumed and column not in ungrouped_columns
    ]
    auto_consumed: set[str] = set()
    ranges = {column: _telemetry_range(primary, column) for column in available}
    units = {column: _telemetry_column_unit(column) for column in available}
    for column in available:
        if column in auto_consumed:
            continue
        auto_consumed.add(column)
        group = [column]
        unit = units[column]
        column_range = ranges[column]
        if unit is not None and column_range is not None:
            for candidate in available:
                if candidate in auto_consumed or units[candidate] != unit:
                    continue
                candidate_range = ranges[candidate]
                if candidate_range is None:
                    continue
                if all(_telemetry_ranges_are_similar(ranges[member] or column_range, candidate_range) for member in group):
                    group.append(candidate)
                    auto_consumed.add(candidate)
        groups.append(
            TelemetryAxisGroup(
                columns=tuple(group),
                title=_telemetry_group_title(group),
                color_index=column_order[group[0]],
            )
        )

    for column in plotted_columns:
        if column in consumed or column in auto_consumed:
            continue
        groups.append(
            TelemetryAxisGroup(
                columns=(column,),
                title=column,
                color_index=column_order[column],
            )
        )

    return sorted(groups, key=lambda group: column_order[group.columns[0]])


def _telemetry_axis_group_count(
    primary: LoadedLog | None,
    columns: Sequence[str],
    manual_axis_groups: Sequence[Sequence[str]] | None = None,
    ungrouped_axis_columns: Sequence[str] | None = None,
) -> int:
    if primary is None:
        return min(len(columns), MAX_RENDERED_TELEMETRY_TRACES)
    return len(
        _resolve_telemetry_axis_groups(
            primary,
            columns,
            manual_axis_groups=manual_axis_groups,
            ungrouped_axis_columns=ungrouped_axis_columns,
        )
    )


def _normalise_gps_scope(
    elapsed_values: Sequence[float],
    start_seconds: float | None,
    end_seconds: float | None,
) -> tuple[float, float] | None:
    if not elapsed_values or start_seconds is None or end_seconds is None:
        return None
    start = _coerce_float(start_seconds)
    end = _coerce_float(end_seconds)
    if start is None or end is None:
        return None
    if not math.isfinite(start) or not math.isfinite(end):
        return None
    if end < start:
        start, end = end, start
    timeline_start = min(elapsed_values)
    timeline_end = max(elapsed_values)
    start = max(timeline_start, min(start, timeline_end))
    end = max(timeline_start, min(end, timeline_end))
    if end <= start:
        return None
    return start, end


def _telemetry_trace_point_budget(plot_width_px: int | None) -> int:
    if plot_width_px is None or plot_width_px <= 0:
        return MAX_TELEMETRY_TRACE_POINTS_HARD_CAP
    budget = int(plot_width_px * TELEMETRY_SAMPLES_PER_PIXEL)
    return max(MIN_TELEMETRY_TRACE_POINTS, min(MAX_TELEMETRY_TRACE_POINTS_HARD_CAP, budget))


def _sample_numeric_series(
    series: pd.Series,
    maximum: int = MAX_TELEMETRY_TRACE_POINTS_HARD_CAP,
) -> tuple[list[int], list[object]]:
    values = pd.to_numeric(series, errors="coerce").to_numpy()
    length = len(values)
    if length <= 0:
        return [], []
    if length <= maximum:
        indices = list(range(length))
        return indices, [values[index] for index in indices]

    bucket_count = max(1, (maximum - 2) // 2)
    indices = _sample_numeric_indices(values, bucket_count)
    if len(indices) > maximum:
        bucket_count = max(1, (maximum - 2) // 3)
        indices = _sample_numeric_indices(values, bucket_count)
    return indices, [values[index] for index in indices]


def _sample_numeric_indices(values: Any, bucket_count: int) -> list[int]:
    length = len(values)
    bucket_size = max(1, math.ceil(length / bucket_count))
    selected = {0, length - 1}
    for start in range(0, length, bucket_size):
        end = min(start + bucket_size, length)
        min_index: int | None = None
        max_index: int | None = None
        missing_index: int | None = None
        min_value = math.inf
        max_value = -math.inf
        for index in range(start, end):
            number = _coerce_float(values[index])
            if number is None:
                if missing_index is None:
                    missing_index = index
                continue
            if number < min_value:
                min_value = number
                min_index = index
            if number > max_value:
                max_value = number
                max_index = index
        if min_index is None or max_index is None:
            if missing_index is not None:
                selected.add(missing_index)
        else:
            selected.add(min_index)
            selected.add(max_index)
            if missing_index is not None:
                selected.add(missing_index)

    return sorted(selected)


def _coerce_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, Real):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, str):
        try:
            number = float(value)
            return number if math.isfinite(number) else None
        except ValueError:
            return None
    return None


def _is_finite_number(value: object) -> bool:
    number = _coerce_float(value)
    return number is not None and math.isfinite(number)


def _numeric_series(df: pd.DataFrame, column: str) -> pd.Series:
    series = df[column]
    if isinstance(series, pd.DataFrame):
        series = series[series.columns[0]]
    numeric = pd.to_numeric(series, errors="coerce")
    return pd.Series(numeric, index=series.index)


def _clean_hex_color(value: str | None, fallback: str) -> str:
    if value and HEX_COLOR_RE.match(value.strip()):
        return value.strip().lower()
    return fallback


def _gps_distance_km(left_lat: float, left_lon: float, right_lat: float, right_lon: float) -> float:
    earth_radius_km = 6371.0088
    left_lat_rad = math.radians(left_lat)
    right_lat_rad = math.radians(right_lat)
    delta_lat = math.radians(right_lat - left_lat)
    delta_lon = math.radians(right_lon - left_lon)
    a = (
        math.sin(delta_lat / 2.0) ** 2
        + math.cos(left_lat_rad) * math.cos(right_lat_rad) * math.sin(delta_lon / 2.0) ** 2
    )
    return 2.0 * earth_radius_km * math.asin(min(1.0, math.sqrt(a)))


class GpsPoint(TypedDict):
    lat: float
    lon: float
    alt: float
    row: int
    elapsedSeconds: float
    value: float | None


def _is_origin_placeholder(lat: float, lon: float) -> bool:
    return abs(lat) <= GPS_ORIGIN_EPSILON and abs(lon) <= GPS_ORIGIN_EPSILON


def _gps_jump_is_plausible(
    left: GpsPoint,
    right: GpsPoint,
    left_time: object | None = None,
    right_time: object | None = None,
) -> bool:
    distance_km = _gps_distance_km(left["lat"], left["lon"], right["lat"], right["lon"])
    if distance_km <= 0.0:
        return True

    # Keep the threshold deliberately wide so the path only splits on clearly
    # invalid GPS jumps instead of on aggressive flight maneuvers.
    return distance_km <= MAX_GPS_JUMP_KM


GpsCandidate = tuple[GpsPoint, object]


def _gps_candidates_are_plausible(left: GpsCandidate, right: GpsCandidate) -> bool:
    return _gps_jump_is_plausible(left[0], right[0], left[1], right[1])


def _drop_isolated_gps_outliers_once(candidates: list[GpsCandidate]) -> list[GpsCandidate]:
    if len(candidates) < 3:
        return candidates

    filtered: list[GpsCandidate] = []
    for index, candidate in enumerate(candidates):
        # Drop single bad samples at the edges or in the middle when both
        # neighboring segments agree that the point is an outlier.
        previous_candidate = candidates[index - 1] if index > 0 else None
        next_candidate = candidates[index + 1] if index + 1 < len(candidates) else None

        if previous_candidate is None and next_candidate is not None and index + 2 < len(candidates):
            second_next_candidate = candidates[index + 2]
            if (
                not _gps_candidates_are_plausible(candidate, next_candidate)
                and _gps_candidates_are_plausible(next_candidate, second_next_candidate)
            ):
                continue
        elif next_candidate is None and previous_candidate is not None and index >= 2:
            second_previous_candidate = candidates[index - 2]
            if (
                not _gps_candidates_are_plausible(previous_candidate, candidate)
                and _gps_candidates_are_plausible(second_previous_candidate, previous_candidate)
            ):
                continue
        elif previous_candidate is not None and next_candidate is not None:
            if (
                not _gps_candidates_are_plausible(previous_candidate, candidate)
                and not _gps_candidates_are_plausible(candidate, next_candidate)
                and _gps_candidates_are_plausible(previous_candidate, next_candidate)
            ):
                continue

        filtered.append(candidate)
    return filtered


def _drop_isolated_gps_outliers(candidates: list[GpsCandidate]) -> list[GpsCandidate]:
    filtered = candidates
    while True:
        next_filtered = _drop_isolated_gps_outliers_once(filtered)
        if len(next_filtered) == len(filtered):
            return filtered
        filtered = next_filtered


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    clean = color.lstrip("#")
    return int(clean[0:2], 16), int(clean[2:4], 16), int(clean[4:6], 16)


def _interpolate_hex_color(start: str, end: str, amount: float) -> str:
    amount = max(0.0, min(1.0, float(amount)))
    start_rgb = _hex_to_rgb(start)
    end_rgb = _hex_to_rgb(end)
    rgb = tuple(round(start_rgb[idx] + (end_rgb[idx] - start_rgb[idx]) * amount) for idx in range(3))
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"


def _gradient_position(
    value: float,
    minimum: float,
    maximum: float,
    midpoint: float | None = None,
    reverse: bool = False,
) -> float:
    if maximum <= minimum:
        amount = 0.5
    elif midpoint is not None and minimum < midpoint < maximum:
        # Split the gradient into two linear ramps so a requested midpoint gets
        # an exact visual anchor instead of being approximated by interpolation.
        if value <= midpoint:
            amount = 0.5 * ((value - minimum) / (midpoint - minimum))
        else:
            amount = 0.5 + 0.5 * ((value - midpoint) / (maximum - midpoint))
    else:
        amount = (value - minimum) / (maximum - minimum)
    amount = max(0.0, min(1.0, amount))
    return 1.0 - amount if reverse else amount


def _format_legend_number(value: float | None) -> str:
    if value is None or not _is_finite_number(value):
        return ""
    value = float(value)
    if abs(value) >= 1000 or (abs(value) < 0.01 and value != 0):
        return f"{value:.3g}"
    return f"{value:.2f}".rstrip("0").rstrip(".")


def build_telemetry_figure(
    primary: LoadedLog | None,
    columns: list[str],
    compare: LoadedLog | None = None,
    selected_index: int | None = None,
    show_grid: bool = True,
    dark: bool = True,
    interaction_mode: str = "zoom",
    time_mode: str = "absolute",
    max_trace_points: int = MAX_TELEMETRY_TRACE_POINTS_HARD_CAP,
    x_axis_range: tuple[object, object] | None = None,
    manual_axis_groups: Sequence[Sequence[str]] | None = None,
    ungrouped_axis_columns: Sequence[str] | None = None,
) -> go.Figure:
    fig = go.Figure()
    if primary is None:
        # Empty state: return a styled placeholder figure so the widget still
        # shows a meaningful prompt before a log is loaded.
        fig.update_layout(
            template="plotly_dark" if dark else "plotly",
            paper_bgcolor="#1f242b" if dark else "#ffffff",
            plot_bgcolor="#171a20" if dark else "#ffffff",
            annotations=[{"text": "Open a log and select telemetry parameters.", "showarrow": False}],
        )
        return fig

    x = _telemetry_x_values(primary, time_mode)
    plotted_columns = columns[:MAX_RENDERED_TELEMETRY_TRACES]
    hidden_trace_count = max(0, len(columns) - len(plotted_columns))
    axis_groups = _resolve_telemetry_axis_groups(
        primary,
        plotted_columns,
        manual_axis_groups=manual_axis_groups,
        ungrouped_axis_columns=ungrouped_axis_columns,
    )
    axis_by_column = {
        column: _axis_name(axis_index)
        for axis_index, group in enumerate(axis_groups)
        for column in group.columns
    }
    for idx, col in enumerate(plotted_columns):
        if col not in primary.dataframe.columns:
            continue
        primary_indices, primary_values = _sample_numeric_series(
            _numeric_series(primary.dataframe, col), maximum=max_trace_points
        )
        primary_x = [x[index] for index in primary_indices] if primary_indices else []
        trace_class = go.Scattergl if len(primary_indices) > 2000 else go.Scatter
        color = COLORS[idx % len(COLORS)]
        axis = axis_by_column.get(col, _axis_name(idx))
        fig.add_trace(
            trace_class(
                x=primary_x,
                y=primary_values,
                customdata=primary_indices,
                name=col,
                mode="lines",
                line={"color": color, "width": 2},
                yaxis=axis,
                showlegend=idx < MAX_VISIBLE_LEGEND_ITEMS,
            )
        )
        if compare is not None and col in compare.dataframe.columns:
            compare_indices, compare_values = _sample_numeric_series(
                _numeric_series(compare.dataframe, col), maximum=max_trace_points
            )
            compare_x_values = _telemetry_x_values(compare, time_mode)
            compare_trace_class = go.Scattergl if len(compare_indices) > 2000 else go.Scatter
            fig.add_trace(
                compare_trace_class(
                    x=[compare_x_values[index] for index in compare_indices],
                    y=compare_values,
                    customdata=compare_indices,
                    name=f"{col} compare",
                    mode="lines",
                    line={"color": color, "width": 1.5, "dash": "dash"},
                    yaxis=axis,
                    opacity=0.75,
                    showlegend=idx < MAX_VISIBLE_LEGEND_ITEMS,
                )
            )

    layout: dict[str, Any] = {
        "template": "plotly_dark" if dark else "plotly",
        "paper_bgcolor": "#1f242b" if dark else "#ffffff",
        "plot_bgcolor": "#171a20" if dark else "#ffffff",
        "hovermode": "x unified",
        "dragmode": interaction_mode if interaction_mode in {"pan", "zoom"} else "zoom",
        "margin": {
            "l": TELEMETRY_MARGIN_LEFT,
            "r": _telemetry_right_margin(len(axis_groups)),
            "t": TELEMETRY_MARGIN_TOP,
            "b": TELEMETRY_MARGIN_BOTTOM,
        },
        "legend": {"orientation": "h", "y": 1.08, "x": 0},
        "xaxis": {
            "title": "Time" if time_mode == "absolute" and primary.time is not None else "Elapsed time",
            "domain": _telemetry_x_axis_domain(len(axis_groups)),
            "showgrid": show_grid,
            "gridcolor": "rgba(255,255,255,0.08)" if dark else "rgba(0,0,0,0.1)",
        },
    }
    if x_axis_range is not None:
        layout["xaxis"]["range"] = [x_axis_range[0], x_axis_range[1]]
    right_axis_positions = _telemetry_right_axis_positions(len(axis_groups))
    for idx, group in enumerate(axis_groups):
        axis_key = "yaxis" if idx == 0 else f"yaxis{idx + 1}"
        axis_is_visible = idx < MAX_VISIBLE_TELEMETRY_AXES
        axis = {
            "title": {
                "text": group.title if axis_is_visible else "",
                "font": {"color": COLORS[group.color_index % len(COLORS)]},
                "standoff": 8,
            },
            "tickfont": {"color": COLORS[group.color_index % len(COLORS)], "size": 10 if idx > 0 else 11},
            "showticklabels": axis_is_visible,
            "ticks": "outside" if axis_is_visible else "",
            "ticklen": 4,
            "automargin": True,
            "showgrid": show_grid and idx == 0,
            "gridcolor": "rgba(255,255,255,0.08)" if dark else "rgba(0,0,0,0.1)",
            "zeroline": False if idx > 0 else True,
        }
        if idx > 0:
            # Overlay secondary axes in a reserved right-side gutter so every
            # plotted telemetry channel keeps its own scale without stacking
            # the readable axes on top of each other.
            position = (
                right_axis_positions[idx - 1]
                if idx - 1 < len(right_axis_positions)
                else 1.0
            )
            axis.update(
                {
                    "anchor": "free",
                    "overlaying": "y",
                    "side": "right",
                    "position": position,
                }
            )
        if not axis_is_visible:
            axis.update({"ticks": "", "showgrid": False, "zeroline": False})
        layout[axis_key] = axis
    fig.update_layout(**layout)
    if not fig.data:
        fig.add_annotation(text="Select telemetry parameters to plot.", showarrow=False)
    if time_mode == "absolute" and primary.time is not None:
        fig.update_xaxes(tickformat="%H:%M:%S", hoverformat="%H:%M:%S")
    else:
        fig.update_xaxes(ticksuffix="s")

    stats_lines = []
    for col in columns:
        if col not in primary.dataframe.columns:
            continue
        series = _numeric_series(primary.dataframe, col)
        minimum = series.min()
        maximum = series.max()
        if pd.isna(minimum) or pd.isna(maximum):
            continue
        stats_lines.append(f"{col}: min {float(minimum):.2f} | max {float(maximum):.2f}")
    if stats_lines:
        visible_stats_lines = stats_lines[:MAX_STATS_ANNOTATION_ROWS]
        hidden_stats_count = len(stats_lines) - len(visible_stats_lines)
        if hidden_stats_count:
            visible_stats_lines.append(f"+ {hidden_stats_count} more selected")
        # Min/max annotations are intentionally compact because they are only a
        # quick readout, not a full statistics table.
        fig.add_annotation(
            x=0.01,
            y=0.99,
            xref="paper",
            yref="paper",
            xanchor="left",
            yanchor="top",
            text="<b>Min / Max</b><br>" + "<br>".join(visible_stats_lines),
            showarrow=False,
            align="left",
            bordercolor="rgba(255,255,255,0.18)" if dark else "rgba(0,0,0,0.2)",
            borderwidth=1,
            bgcolor="rgba(0,0,0,0.58)" if dark else "rgba(255,255,255,0.86)",
            font={"size": 12},
        )
    if hidden_trace_count:
        fig.add_annotation(
            x=0.99,
            y=0.99,
            xref="paper",
            yref="paper",
            xanchor="right",
            yanchor="top",
            text=f"Showing first {len(plotted_columns)} of {len(columns)} selected",
            showarrow=False,
            align="right",
            bordercolor="rgba(255,255,255,0.18)" if dark else "rgba(0,0,0,0.2)",
            borderwidth=1,
            bgcolor="rgba(0,0,0,0.58)" if dark else "rgba(255,255,255,0.86)",
            font={"size": 12},
        )

    if selected_index is not None and x:
        # Mark the currently selected sample so the plot aligns with the cursor
        # panel and any compare-log delta readout.
        selected_index = max(0, min(selected_index, len(x) - 1))
        fig.add_vline(x=x[selected_index], line_width=2, line_color="#ffffff" if dark else "#1f2937", opacity=0.55)
    return fig


def _telemetry_x_values(log: LoadedLog, time_mode: str) -> Sequence[pd.Timestamp | float]:
    if time_mode == "absolute" and log.time is not None and log.time.notna().any():
        return list(log.time.ffill().bfill())
    if time_mode == "relative_time" and log.time is not None and log.time.notna().any():
        # Keep the axis formatted like a clock while shifting the series to a
        # stable epoch anchor so relative timestamps still render cleanly.
        time_series = log.time.ffill().bfill()
        start = time_series.iloc[0]
        return list(pd.Timestamp("1970-01-01") + (time_series - start))
    return relative_seconds(log)


def build_gps_figure(log: LoadedLog | None, color_column: str | None = None, dark: bool = True) -> go.Figure:
    fig = go.Figure()
    if log is None:
        fig.update_layout(
            template="plotly_dark" if dark else "plotly",
            annotations=[{"text": "Open a log with GPS data to view the flight path.", "showarrow": False}],
        )
        return fig

    gps = log.gps_columns
    if gps is None:
        fig.update_layout(
            template="plotly_dark" if dark else "plotly",
            annotations=[{"text": "No GPS latitude/longitude columns detected.", "showarrow": False}],
        )
        return fig

    df = log.dataframe
    # Use altitude as the z-axis when available; otherwise fall back to sample
    # order so the 3D trace still has a meaningful depth dimension.
    z = _numeric_series(df, gps.altitude) if gps.altitude and gps.altitude in df.columns else list(range(len(df)))
    lat_title = gps.latitude_label or gps.latitude
    lon_title = gps.longitude_label or gps.longitude
    alt_title = gps.altitude_label or gps.altitude or "Sample"
    marker: dict[str, object] = {"size": 3, "color": "#55d977"}
    if color_column and color_column in df.columns:
        marker = {
            "size": 4,
            "color": pd.to_numeric(df[color_column], errors="coerce"),
            "colorscale": "Viridis",
            "showscale": True,
            "colorbar": {"title": color_column},
        }
    fig.add_trace(
        go.Scatter3d(
            x=pd.to_numeric(df[gps.longitude], errors="coerce"),
            y=pd.to_numeric(df[gps.latitude], errors="coerce"),
            z=z,
            mode="lines+markers",
            line={"color": "#48c774", "width": 5},
            marker=marker,
            text=[f"Row {i}" for i in range(len(df))],
            name="Flight path",
        )
    )
    fig.update_layout(
        template="plotly_dark" if dark else "plotly",
        paper_bgcolor="#1f242b" if dark else "#ffffff",
        margin={"l": 0, "r": 0, "t": 24, "b": 0},
        scene={
            "xaxis_title": lon_title,
            "yaxis_title": lat_title,
            "zaxis_title": alt_title,
        },
    )
    return fig


def _empty_gps_payload(
    message: str,
    scope: tuple[float, float] | None = None,
) -> dict[str, object]:
    start_elapsed = scope[0] if scope is not None else 0.0
    end_elapsed = scope[1] if scope is not None else 0.0
    return {
        "status": "empty",
        "message": message,
        "points": [],
        "segments": [],
        "legend": {"enabled": False},
        "timeline": {
            "enabled": False,
            "durationSeconds": max(0.0, end_elapsed - start_elapsed),
            "startElapsedSeconds": start_elapsed,
            "endElapsedSeconds": end_elapsed,
            "rows": 0,
        },
        "altitudeLabel": "Altitude",
        "altitudeStats": {"label": "Altitude", "minimum": None, "maximum": None, "baseMeters": 0.0},
        "altitudeScale": ALTITUDE_EXAGGERATION,
        "altitudeFloorMeters": ALTITUDE_FLOOR_METERS,
        "mapMaxZoom": MAP_MAX_ZOOM,
        "mapFitMaxZoom": MAP_FIT_MAX_ZOOM,
        "mapMaxPitch": MAP_MAX_PITCH,
        "rasterTileUrls": [OPENSTREETMAP_RASTER_TILE_URL],
        "rasterTileMaxZoom": OPENSTREETMAP_RASTER_TILE_MAX_ZOOM,
    }


def build_gps_map_payload(log: LoadedLog | None, options: GpsGradientOptions | None = None) -> dict[str, object]:
    options = options or GpsGradientOptions()
    if log is None:
        return _empty_gps_payload("Open a log with GPS data to view the flight path.")

    gps = log.gps_columns
    if gps is None:
        return _empty_gps_payload("No GPS latitude/longitude columns detected.")

    df = log.dataframe
    lat_values = _numeric_series(df, gps.latitude)
    lon_values = _numeric_series(df, gps.longitude)
    time_values = list(log.time) if log.time is not None else [None] * len(df)
    elapsed_values = relative_seconds(log)
    active_scope = _normalise_gps_scope(
        elapsed_values,
        options.scope_start_seconds,
        options.scope_end_seconds,
    )
    if gps.altitude and gps.altitude in df.columns:
        alt_values = _numeric_series(df, gps.altitude)
        altitude_label = gps.altitude_label or gps.altitude
    else:
        alt_values = pd.Series([0.0] * len(df), index=df.index, dtype="float64")
        altitude_label = "Altitude"

    color_column = options.color_column if options.color_column in df.columns else None
    color_values = _numeric_series(df, color_column) if color_column else None
    candidate_points: list[GpsCandidate] = []
    for row_index in range(len(df)):
        lat = _coerce_float(lat_values.iloc[row_index])
        lon = _coerce_float(lon_values.iloc[row_index])
        if lat is None or lon is None:
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        # Drop zeroed placeholders before they can pollute bounds or create a
        # fake jump at the start of the flight.
        if _is_origin_placeholder(lat, lon):
            continue
        alt = _coerce_float(alt_values.iloc[row_index])
        point: GpsPoint = {
            "lat": lat,
            "lon": lon,
            "alt": alt if alt is not None else 0.0,
            "row": row_index + 1,
            "elapsedSeconds": elapsed_values[row_index] if row_index < len(elapsed_values) else float(row_index),
            "value": None,
        }
        if active_scope is not None:
            elapsed = point["elapsedSeconds"]
            if elapsed < active_scope[0] or elapsed > active_scope[1]:
                continue
        if color_values is not None:
            point["value"] = _coerce_float(color_values.iloc[row_index])
        candidate_points.append((point, time_values[row_index] if row_index < len(time_values) else None))

    if len(candidate_points) < 2:
        if active_scope is not None:
            return _empty_gps_payload(
                "GPS data was detected, but fewer than two valid coordinate rows were found in the visible telemetry range.",
                active_scope,
            )
        return _empty_gps_payload("GPS data was detected, but fewer than two valid coordinate rows were found.")

    # Run the outlier pass before segmenting so isolated bad samples disappear
    # instead of forcing an unnecessary path break.
    candidate_points = _drop_isolated_gps_outliers(candidate_points)
    if len(candidate_points) < 2:
        if active_scope is not None:
            return _empty_gps_payload(
                "GPS data was detected, but fewer than two valid coordinate rows were found in the visible telemetry range.",
                active_scope,
            )
        return _empty_gps_payload("GPS data was detected, but fewer than two valid coordinate rows were found.")

    path_parts: list[list[GpsCandidate]] = []
    current_part: list[GpsCandidate] = [candidate_points[0]]
    for point, point_time in candidate_points[1:]:
        previous_point, previous_time = current_part[-1]
        if _gps_jump_is_plausible(previous_point, point, previous_time, point_time):
            current_part.append((point, point_time))
        else:
            # A large jump means the track probably restarted, so split the
            # polyline instead of drawing a line across the gap.
            path_parts.append(current_part)
            current_part = [(point, point_time)]
    path_parts.append(current_part)

    rendered_parts = [part for part in path_parts if len(part) >= 2]
    if not rendered_parts:
        if active_scope is not None:
            return _empty_gps_payload(
                "GPS data was detected, but fewer than two valid coordinate rows were found in the visible telemetry range.",
                active_scope,
            )
        return _empty_gps_payload("GPS data was detected, but fewer than two valid coordinate rows were found.")

    points: list[GpsPoint] = [point for part in rendered_parts for point, _ in part]

    start_color = _clean_hex_color(options.start_color, GpsGradientOptions.start_color)
    end_color = _clean_hex_color(options.end_color, GpsGradientOptions.end_color)
    valid_values: list[float] = []
    for point in points:
        value = point["value"]
        if value is not None:
            valid_values.append(value)
    minimum: float | None = None
    maximum: float | None = None
    if color_column and valid_values:
        if options.auto_range:
            minimum = min(valid_values)
            maximum = max(valid_values)
        else:
            range_min = _coerce_float(options.range_min)
            range_max = _coerce_float(options.range_max)
            minimum = range_min if range_min is not None and math.isfinite(range_min) else min(valid_values)
            maximum = range_max if range_max is not None and math.isfinite(range_max) else max(valid_values)
            if maximum < minimum:
                minimum, maximum = maximum, minimum
    midpoint = _coerce_float(options.midpoint)
    if midpoint is not None and not math.isfinite(midpoint):
        midpoint = None

    segments: list[dict[str, object]] = []
    path_parts_json: list[list[dict[str, object]]] = []
    for part in rendered_parts:
        part_points = [dict(point) for point, _ in part]
        path_parts_json.append(part_points)
        for left, right in zip(part, part[1:]):
            left_point, left_time = left
            right_point, right_time = right
            segment_values: list[float] = []
            for point in (left_point, right_point):
                value = point["value"]
                if value is not None:
                    segment_values.append(value)
            segment_value = sum(segment_values) / len(segment_values) if segment_values else None
            if minimum is not None and maximum is not None and segment_value is not None:
                amount = _gradient_position(segment_value, minimum, maximum, midpoint=midpoint, reverse=options.reverse)
                color = _interpolate_hex_color(start_color, end_color, amount)
            elif color_column:
                # If the user asked for a color column but this segment has no
                # numeric value, show that gap explicitly instead of guessing.
                color = MISSING_VALUE_COLOR
            else:
                color = DEFAULT_PATH_COLOR
            segments.append(
                {
                    "left": dict(left_point),
                    "right": dict(right_point),
                    "positions": [
                        left_point["lon"],
                        left_point["lat"],
                        left_point["alt"],
                        right_point["lon"],
                        right_point["lat"],
                        right_point["alt"],
                    ],
                    "color": color,
                    "startRow": left_point["row"],
                    "endRow": right_point["row"],
                    "value": segment_value,
                }
            )

    legend: dict[str, object] = {"enabled": False}
    if color_column and minimum is not None and maximum is not None:
        # The map legend mirrors the chosen gradient so the user can interpret
        # the color ramp without opening a separate settings dialog.
        low_color = end_color if options.reverse else start_color
        high_color = start_color if options.reverse else end_color
        legend = {
            "enabled": True,
            "label": color_column,
            "minimum": minimum,
            "maximum": maximum,
            "midpoint": midpoint if midpoint is not None and minimum < midpoint < maximum else None,
            "minLabel": _format_legend_number(minimum),
            "maxLabel": _format_legend_number(maximum),
            "midLabel": _format_legend_number(midpoint) if midpoint is not None and minimum < midpoint < maximum else "",
            "lowColor": low_color,
            "highColor": high_color,
        }

    valid_altitudes = [point["alt"] for point in points if math.isfinite(point["alt"])]
    altitude_minimum = min(valid_altitudes) if valid_altitudes else 0.0
    altitude_maximum = max(valid_altitudes) if valid_altitudes else 0.0
    timeline_start = active_scope[0] if active_scope is not None else 0.0
    timeline_end = active_scope[1] if active_scope is not None else (max(elapsed_values) if elapsed_values else 0.0)

    return {
        "status": "ok",
        "message": "",
        "points": points,
        "pathParts": path_parts_json,
        "segments": segments,
        "legend": legend,
        "timeline": {
            "enabled": bool(points),
            "durationSeconds": max(0.0, timeline_end - timeline_start),
            "startElapsedSeconds": timeline_start,
            "endElapsedSeconds": timeline_end,
            "rows": len(points),
        },
        "latitudeLabel": gps.latitude_label or gps.latitude,
        "longitudeLabel": gps.longitude_label or gps.longitude,
        "altitudeLabel": altitude_label,
        "altitudeStats": {
            "label": altitude_label,
            "minimum": altitude_minimum,
            "maximum": altitude_maximum,
            "baseMeters": altitude_minimum,
        },
        "altitudeScale": ALTITUDE_EXAGGERATION,
        "altitudeFloorMeters": ALTITUDE_FLOOR_METERS,
        "mapMaxZoom": MAP_MAX_ZOOM,
        "mapFitMaxZoom": MAP_FIT_MAX_ZOOM,
        "mapMaxPitch": MAP_MAX_PITCH,
        "rasterTileUrls": [OPENSTREETMAP_RASTER_TILE_URL],
        "rasterTileMaxZoom": OPENSTREETMAP_RASTER_TILE_MAX_ZOOM,
    }


def build_gps_map_html(
    log: LoadedLog | None,
    options: GpsGradientOptions | None = None,
    dark: bool = True,
) -> str:
    payload = build_gps_map_payload(log, options)
    return render_gps_map_html(payload, dark=dark)


def figure_html(fig: go.Figure, bridge: bool = False, dark: bool = True) -> str:
    body = fig.to_html(
        include_plotlyjs=False,
        full_html=False,
        config={"responsive": True, "scrollZoom": True, "displaylogo": False},
    )
    background = "#1f242b" if dark else "#ffffff"
    plotly_script = _plotly_script_tag()
    if not bridge:
        return f"<html><head>{plotly_script}</head><body style='margin:0;background:{background}'>{body}</body></html>"
    # The bridge script is only injected for the Qt WebChannel path so the
    # plot can forward clicks and keyboard navigation back to the main window.
    return f"""
<html>
<head>
  {plotly_script}
  <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
  <style>html,body{{height:100%;margin:0;background:{background};overflow:hidden}}</style>
</head>
<body>
{body}
<script>
let bridge = null;
new QWebChannel(qt.webChannelTransport, function(channel) {{
  bridge = channel.objects.plotBridge;
}});
function plotDiv() {{
  return document.querySelector('.plotly-graph-div');
}}
function bindPlot() {{
  const plot = plotDiv();
  if (!plot) return;
  let relayoutTimer = null;
  plot.on('plotly_click', function(data) {{
    if (!bridge || !data.points || !data.points.length) return;
    const point = data.points[0];
    const selected = Number.isFinite(point.customdata) ? point.customdata : point.pointIndex;
    bridge.selectIndex(selected);
  }});
  plot.on('plotly_relayout', function(event) {{
    if (!bridge || !event) return;
    let start = null;
    let end = null;
    if (event['xaxis.autorange'] === true) {{
      start = null;
      end = null;
    }} else if (Array.isArray(event['xaxis.range']) && event['xaxis.range'].length >= 2) {{
      start = event['xaxis.range'][0];
      end = event['xaxis.range'][1];
    }} else if (Object.prototype.hasOwnProperty.call(event, 'xaxis.range[0]') &&
               Object.prototype.hasOwnProperty.call(event, 'xaxis.range[1]')) {{
      start = event['xaxis.range[0]'];
      end = event['xaxis.range[1]'];
    }} else {{
      return;
    }}
    window.clearTimeout(relayoutTimer);
    relayoutTimer = window.setTimeout(function() {{
      if (bridge && typeof bridge.setXRange === 'function') {{
        bridge.setXRange(JSON.stringify([start, end]));
      }}
    }}, 100);
  }});
  document.addEventListener('keydown', function(event) {{
    if (!bridge) return;
    if (event.key === 'ArrowLeft') bridge.stepIndex(-1);
    if (event.key === 'ArrowRight') bridge.stepIndex(1);
  }});
}}
setTimeout(bindPlot, 200);
</script>
</body>
</html>
"""

