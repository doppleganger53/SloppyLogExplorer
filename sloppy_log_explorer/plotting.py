"""Plotly chart and GPS map rendering helpers."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from numbers import Real
from typing import Any, TypedDict

import pandas as pd
import plotly.graph_objects as go

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
MAX_GPS_JUMP_KM = 1000.0
# A tiny origin tolerance catches placeholder zeros without rejecting genuine
# coordinates that are only close to zero.
GPS_ORIGIN_EPSILON = 1e-9


def _axis_name(index: int) -> str:
    return "y" if index == 0 else f"y{index + 1}"


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
    interaction_mode: str = "pan",
    time_mode: str = "absolute",
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
    for idx, col in enumerate(columns):
        if col not in primary.dataframe.columns:
            continue
        color = COLORS[idx % len(COLORS)]
        axis = _axis_name(idx)
        fig.add_trace(
            go.Scatter(
                x=x,
                y=pd.to_numeric(primary.dataframe[col], errors="coerce"),
                name=col,
                mode="lines",
                line={"color": color, "width": 2},
                yaxis=axis,
            )
        )
        if compare is not None and col in compare.dataframe.columns:
            fig.add_trace(
                go.Scatter(
                    x=_telemetry_x_values(compare, time_mode),
                    y=pd.to_numeric(compare.dataframe[col], errors="coerce"),
                    name=f"{col} compare",
                    mode="lines",
                    line={"color": color, "width": 1.5, "dash": "dash"},
                    yaxis=axis,
                    opacity=0.75,
                )
            )

    layout: dict[str, Any] = {
        "template": "plotly_dark" if dark else "plotly",
        "paper_bgcolor": "#1f242b" if dark else "#ffffff",
        "plot_bgcolor": "#171a20" if dark else "#ffffff",
        "hovermode": "x unified",
        "dragmode": interaction_mode if interaction_mode in {"pan", "zoom"} else "pan",
        "margin": {"l": 58, "r": 64 + max(0, len(columns) - 1) * 44, "t": 30, "b": 46},
        "legend": {"orientation": "h", "y": 1.08, "x": 0},
        "xaxis": {
            "title": "Time" if time_mode == "absolute" and primary.time is not None else "Elapsed time",
            "showgrid": show_grid,
            "gridcolor": "rgba(255,255,255,0.08)" if dark else "rgba(0,0,0,0.1)",
        },
    }
    for idx, col in enumerate(columns):
        axis_key = "yaxis" if idx == 0 else f"yaxis{idx + 1}"
        axis = {
            "title": {"text": col, "font": {"color": COLORS[idx % len(COLORS)]}},
            "tickfont": {"color": COLORS[idx % len(COLORS)]},
            "showgrid": show_grid and idx == 0,
            "gridcolor": "rgba(255,255,255,0.08)" if dark else "rgba(0,0,0,0.1)",
        }
        if idx > 0:
            # Overlay secondary axes on the right edge so every selected
            # telemetry channel keeps its own scale without shrinking the plot.
            axis.update(
                {
                    "anchor": "free",
                    "overlaying": "y",
                    "side": "right",
                    "position": min(0.98, 0.86 + idx * 0.045),
                }
            )
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
        # Min/max annotations are intentionally compact because they are only a
        # quick readout, not a full statistics table.
        fig.add_annotation(
            x=0.01,
            y=0.99,
            xref="paper",
            yref="paper",
            xanchor="left",
            yanchor="top",
            text="<b>Min / Max</b><br>" + "<br>".join(stats_lines),
            showarrow=False,
            align="left",
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


def _empty_gps_payload(message: str) -> dict[str, object]:
    return {
        "status": "empty",
        "message": message,
        "points": [],
        "segments": [],
        "legend": {"enabled": False},
        "timeline": {
            "enabled": False,
            "durationSeconds": 0.0,
            "startElapsedSeconds": 0.0,
            "endElapsedSeconds": 0.0,
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
        if color_values is not None:
            point["value"] = _coerce_float(color_values.iloc[row_index])
        candidate_points.append((point, time_values[row_index] if row_index < len(time_values) else None))

    if len(candidate_points) < 2:
        return _empty_gps_payload("GPS data was detected, but fewer than two valid coordinate rows were found.")

    # Run the outlier pass before segmenting so isolated bad samples disappear
    # instead of forcing an unnecessary path break.
    candidate_points = _drop_isolated_gps_outliers(candidate_points)
    if len(candidate_points) < 2:
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
    timeline_end = max(elapsed_values) if elapsed_values else 0.0

    return {
        "status": "ok",
        "message": "",
        "points": points,
        "pathParts": path_parts_json,
        "segments": segments,
        "legend": legend,
        "timeline": {
            "enabled": bool(points),
            "durationSeconds": timeline_end,
            "startElapsedSeconds": 0.0,
            "endElapsedSeconds": timeline_end,
            "rows": len(df),
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
        include_plotlyjs="cdn",
        full_html=False,
        config={"responsive": True, "scrollZoom": True, "displaylogo": False},
    )
    background = "#1f242b" if dark else "#ffffff"
    if not bridge:
        return f"<html><body style='margin:0;background:{background}'>{body}</body></html>"
    # The bridge script is only injected for the Qt WebChannel path so the
    # plot can forward clicks and keyboard navigation back to the main window.
    return f"""
<html>
<head>
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
  plot.on('plotly_click', function(data) {{
    if (!bridge || !data.points || !data.points.length) return;
    bridge.selectIndex(data.points[0].pointIndex);
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

