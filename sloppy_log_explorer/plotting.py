from __future__ import annotations

import html
import json
import math
import re

import pandas as pd
import plotly.graph_objects as go

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

CESIUM_VERSION = "1.141"
CESIUM_BASE_URL = f"https://cesium.com/downloads/cesiumjs/releases/{CESIUM_VERSION}/Build/Cesium"
OSM_TILE_URL = "https://tile.openstreetmap.org/"
DEFAULT_PATH_COLOR = "#55d977"
MISSING_VALUE_COLOR = "#9ca3af"
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _axis_name(index: int) -> str:
    return "y" if index == 0 else f"y{index + 1}"


def _is_finite_number(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _clean_hex_color(value: str | None, fallback: str) -> str:
    if value and HEX_COLOR_RE.match(value.strip()):
        return value.strip().lower()
    return fallback


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

    layout: dict[str, object] = {
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
        series = pd.to_numeric(primary.dataframe[col], errors="coerce")
        minimum = series.min()
        maximum = series.max()
        if pd.isna(minimum) or pd.isna(maximum):
            continue
        stats_lines.append(f"{col}: min {minimum:.2f} | max {maximum:.2f}")
    if stats_lines:
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
        selected_index = max(0, min(selected_index, len(x) - 1))
        fig.add_vline(x=x[selected_index], line_width=2, line_color="#ffffff" if dark else "#1f2937", opacity=0.55)
    return fig


def _telemetry_x_values(log: LoadedLog, time_mode: str) -> list[object]:
    if time_mode == "absolute" and log.time is not None and log.time.notna().any():
        return list(log.time.ffill().bfill())
    if time_mode == "relative_time" and log.time is not None and log.time.notna().any():
        start = log.time.ffill().bfill().iloc[0]
        return list(pd.Timestamp("1970-01-01") + (log.time.ffill().bfill() - start))
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
    z = pd.to_numeric(df[gps.altitude], errors="coerce") if gps.altitude else list(range(len(df)))
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
        "osmUrl": OSM_TILE_URL,
    }


def build_gps_map_payload(log: LoadedLog | None, options: GpsGradientOptions | None = None) -> dict[str, object]:
    options = options or GpsGradientOptions()
    if log is None:
        return _empty_gps_payload("Open a log with GPS data to view the flight path.")

    gps = log.gps_columns
    if gps is None:
        return _empty_gps_payload("No GPS latitude/longitude columns detected.")

    df = log.dataframe
    lat_values = pd.to_numeric(df[gps.latitude], errors="coerce")
    lon_values = pd.to_numeric(df[gps.longitude], errors="coerce")
    if gps.altitude and gps.altitude in df.columns:
        alt_values = pd.to_numeric(df[gps.altitude], errors="coerce")
        altitude_label = gps.altitude_label or gps.altitude
    else:
        alt_values = pd.Series([0.0] * len(df), index=df.index, dtype="float64")
        altitude_label = "Altitude"

    color_column = options.color_column if options.color_column in df.columns else None
    color_values = pd.to_numeric(df[color_column], errors="coerce") if color_column else None
    points: list[dict[str, object]] = []
    for row_index in range(len(df)):
        lat = lat_values.iloc[row_index]
        lon = lon_values.iloc[row_index]
        if not (_is_finite_number(lat) and _is_finite_number(lon)):
            continue
        lat = float(lat)
        lon = float(lon)
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        alt = alt_values.iloc[row_index]
        point: dict[str, object] = {
            "lat": lat,
            "lon": lon,
            "alt": float(alt) if _is_finite_number(alt) else 0.0,
            "row": row_index + 1,
        }
        if color_values is not None:
            value = color_values.iloc[row_index]
            point["value"] = float(value) if _is_finite_number(value) else None
        points.append(point)

    if len(points) < 2:
        return _empty_gps_payload("GPS data was detected, but fewer than two valid coordinate rows were found.")

    start_color = _clean_hex_color(options.start_color, GpsGradientOptions.start_color)
    end_color = _clean_hex_color(options.end_color, GpsGradientOptions.end_color)
    valid_values = [float(point["value"]) for point in points if point.get("value") is not None]
    minimum: float | None = None
    maximum: float | None = None
    if color_column and valid_values:
        if options.auto_range:
            minimum = min(valid_values)
            maximum = max(valid_values)
        else:
            minimum = float(options.range_min) if _is_finite_number(options.range_min) else min(valid_values)
            maximum = float(options.range_max) if _is_finite_number(options.range_max) else max(valid_values)
            if maximum < minimum:
                minimum, maximum = maximum, minimum
    midpoint = float(options.midpoint) if _is_finite_number(options.midpoint) else None

    segments: list[dict[str, object]] = []
    for left, right in zip(points, points[1:]):
        segment_values = [float(point["value"]) for point in (left, right) if point.get("value") is not None]
        segment_value = sum(segment_values) / len(segment_values) if segment_values else None
        if minimum is not None and maximum is not None and segment_value is not None:
            amount = _gradient_position(segment_value, minimum, maximum, midpoint=midpoint, reverse=options.reverse)
            color = _interpolate_hex_color(start_color, end_color, amount)
        elif color_column:
            color = MISSING_VALUE_COLOR
        else:
            color = DEFAULT_PATH_COLOR
        segments.append(
            {
                "positions": [
                    float(left["lon"]),
                    float(left["lat"]),
                    float(left["alt"]),
                    float(right["lon"]),
                    float(right["lat"]),
                    float(right["alt"]),
                ],
                "color": color,
                "startRow": int(left["row"]),
                "endRow": int(right["row"]),
                "value": segment_value,
            }
        )

    legend: dict[str, object] = {"enabled": False}
    if color_column and minimum is not None and maximum is not None:
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

    return {
        "status": "ok",
        "message": "",
        "points": points,
        "segments": segments,
        "legend": legend,
        "latitudeLabel": gps.latitude_label or gps.latitude,
        "longitudeLabel": gps.longitude_label or gps.longitude,
        "altitudeLabel": altitude_label,
        "osmUrl": OSM_TILE_URL,
    }


def _gps_message_html(message: str, dark: bool) -> str:
    background = "#1f242b" if dark else "#ffffff"
    color = "#e5e7eb" if dark else "#1f2937"
    border = "#3a414d" if dark else "#d1d5db"
    return f"""
<html>
<body style="margin:0;background:{background};color:{color};font-family:Arial,sans-serif;">
  <div style="height:100vh;display:flex;align-items:center;justify-content:center;text-align:center;">
    <div style="border:1px solid {border};border-radius:6px;padding:18px 22px;max-width:520px;">
      {html.escape(message)}
    </div>
  </div>
</body>
</html>
"""


def build_gps_map_html(log: LoadedLog | None, options: GpsGradientOptions | None = None, dark: bool = True) -> str:
    payload = build_gps_map_payload(log, options)
    if payload.get("status") != "ok":
        return _gps_message_html(str(payload.get("message") or "No GPS path to display."), dark)

    background = "#1f242b" if dark else "#ffffff"
    panel_bg = "rgba(21,24,29,0.88)" if dark else "rgba(255,255,255,0.92)"
    panel_fg = "#e5e7eb" if dark else "#1f2937"
    border = "rgba(255,255,255,0.18)" if dark else "rgba(0,0,0,0.18)"
    data_json = json.dumps(payload, allow_nan=False)
    return f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css">
  <script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    window.CESIUM_BASE_URL = "{CESIUM_BASE_URL}/";
  </script>
  <link href="{CESIUM_BASE_URL}/Widgets/widgets.css" rel="stylesheet">
  <script src="{CESIUM_BASE_URL}/Cesium.js"></script>
  <style>
    html, body {{
      width: 100%;
      height: 100%;
      margin: 0;
      padding: 0;
      overflow: hidden;
      background: {background};
      font-family: Arial, sans-serif;
    }}
    body {{
      position: relative;
    }}
    #cesiumContainer,
    #leafletContainer {{
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
    }}
    #leafletContainer[hidden] {{
      display: none;
    }}
    .leaflet-container {{
      background: {background};
      font: inherit;
    }}
    .leaflet-tooltip.gpsMarkerTooltip {{
      background: rgba(17, 24, 39, 0.92);
      border: 1px solid rgba(255, 255, 255, 0.18);
      color: #f9fafb;
      box-shadow: none;
      font: 12px Arial, sans-serif;
      padding: 2px 8px;
    }}
    .leaflet-tooltip.gpsMarkerTooltip::before {{
      border-top-color: rgba(17, 24, 39, 0.92);
      border-bottom-color: rgba(17, 24, 39, 0.92);
    }}
    #legend {{
      position: absolute;
      left: 12px;
      bottom: 22px;
      min-width: 260px;
      max-width: 380px;
      color: {panel_fg};
      background: {panel_bg};
      border: 1px solid {border};
      border-radius: 6px;
      padding: 10px 12px;
      box-sizing: border-box;
      font-size: 12px;
      z-index: 10;
    }}
    #legendTitle {{
      font-weight: 700;
      margin-bottom: 7px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    #gradientBar {{
      height: 12px;
      border-radius: 4px;
      border: 1px solid {border};
      margin-bottom: 5px;
    }}
    #legendLabels {{
      display: flex;
      justify-content: space-between;
      gap: 8px;
    }}
    #statusOverlay {{
      position: absolute;
      left: 8px;
      top: 8px;
      z-index: 12;
      min-width: 160px;
      max-width: 320px;
      font-size: 11px;
      line-height: 1.3;
      color: {panel_fg};
      background: {panel_bg};
      border: 1px solid {border};
      border-radius: 5px;
      padding: 6px 8px;
      box-sizing: border-box;
      pointer-events: none;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    #statusOverlay[data-kind="error"] {{
      border-color: rgba(239,68,68,0.85);
      color: #fecaca;
      background: rgba(127,29,29,0.9);
    }}
    #statusOverlay[data-kind="ok"] {{
      color: #dcfce7;
      background: rgba(20,83,45,0.88);
      border-color: rgba(74,222,128,0.65);
    }}
    #osmAttribution {{
      position: absolute;
      right: 8px;
      bottom: 2px;
      z-index: 10;
      font-size: 11px;
      color: #111827;
      background: rgba(255,255,255,0.84);
      padding: 2px 4px;
      border-radius: 3px;
    }}
    #osmAttribution a {{ color: #0645ad; }}
  </style>
</head>
<body>
  <div id="cesiumContainer"></div>
  <div id="leafletContainer" hidden></div>
  <div id="legend" hidden>
    <div id="legendTitle"></div>
    <div id="gradientBar"></div>
    <div id="legendLabels">
      <span id="legendMin"></span>
      <span id="legendMid"></span>
      <span id="legendMax"></span>
    </div>
  </div>
  <div id="statusOverlay" hidden></div>
  <div id="osmAttribution">
    Tiles &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>
  </div>
    <script>
        const flightData = {data_json};
        const osmTileUrl = flightData.osmUrl.endsWith("/")
      ? flightData.osmUrl + "{{z}}/{{x}}/{{y}}.png"
      : flightData.osmUrl + "/{{z}}/{{x}}/{{y}}.png";
    const cesiumContainer = document.getElementById("cesiumContainer");
    const leafletContainer = document.getElementById("leafletContainer");
    const legendContainer = document.getElementById("legend");
    const legendTitle = document.getElementById("legendTitle");
    const gradientBar = document.getElementById("gradientBar");
    const legendMin = document.getElementById("legendMin");
    const legendMid = document.getElementById("legendMid");
    const legendMax = document.getElementById("legendMax");
    const statusOverlay = document.getElementById("statusOverlay");
    let statusHideTimer = null;
    let cesiumViewer = null;
    let leafletMap = null;
    let readinessTimer = null;
    let cesiumReady = false;
    let activeMode = "cesium";
    const preferLeafletRenderer = /QtWebEngine/i.test(navigator.userAgent || "");

    function setStatus(message, kind) {{
      if (!statusOverlay) return;
      if (statusHideTimer) {{
        clearTimeout(statusHideTimer);
        statusHideTimer = null;
      }}
      if (!message) {{
        statusOverlay.hidden = true;
        statusOverlay.textContent = "";
        statusOverlay.dataset.kind = "";
        return;
      }}
      statusOverlay.hidden = false;
      statusOverlay.dataset.kind = kind || "info";
      statusOverlay.textContent = message;
    }}

    function transientStatus(message, kind, timeoutMs) {{
      setStatus(message, kind);
      if ((kind || "info") === "error") {{
        return;
      }}
      statusHideTimer = setTimeout(() => {{
        if (statusOverlay && statusOverlay.dataset.kind === (kind || "info")) {{
          setStatus("", "");
        }}
      }}, timeoutMs || 1800);
    }}

    function renderLegend() {{
      const legend = flightData.legend;
      if (!legend || !legend.enabled) {{
        legendContainer.hidden = true;
        return;
      }}
      legendContainer.hidden = false;
      legendTitle.textContent = `Color by ${{legend.label}}`;
      gradientBar.style.background = `linear-gradient(90deg, ${{legend.lowColor}}, ${{legend.highColor}})`;
      legendMin.textContent = legend.minLabel;
      legendMid.textContent = legend.midLabel || "";
      legendMax.textContent = legend.maxLabel;
    }}

    function destroyCesiumViewer() {{
      if (!cesiumViewer) {{
        return;
      }}
      try {{
        cesiumViewer.destroy();
      }} catch (error) {{
        console.warn("Failed to destroy Cesium viewer", error);
      }}
      cesiumViewer = null;
    }}

    function clearReadinessTimer() {{
      if (readinessTimer) {{
        clearTimeout(readinessTimer);
        readinessTimer = null;
      }}
    }}

    function markCesiumReady() {{
      if (cesiumReady || activeMode !== "cesium") {{
        return;
      }}
      cesiumReady = true;
      clearReadinessTimer();
      transientStatus("Map ready", "ok", 1200);
    }}

    function buildLeafletMap() {{
      if (!window.L) {{
        throw new Error("Leaflet library failed to load");
      }}
      leafletContainer.hidden = false;
      cesiumContainer.hidden = true;
      const map = L.map("leafletContainer", {{
        attributionControl: false,
        zoomControl: true,
        preferCanvas: true
      }});
      const tileLayer = L.tileLayer(osmTileUrl, {{
        maxZoom: 19,
        tileSize: 256,
        updateWhenIdle: true
      }});
      tileLayer.on("loading", () => {{
        if (activeMode === "leaflet") {{
          transientStatus("Loading map tiles...", "info", 900);
        }}
      }});
      tileLayer.on("load", () => {{
        if (activeMode === "leaflet") {{
          transientStatus("Map ready", "ok", 1200);
        }}
      }});
      tileLayer.on("tileerror", (event) => {{
        const details = event && event.error && (event.error.message || String(event.error));
        setStatus("OSM tile error" + (details ? ": " + details : ""), "error");
      }});
      tileLayer.addTo(map);

      const pathLatLngs = flightData.points.map((point) => [point.lat, point.lon]);
      L.polyline(pathLatLngs, {{
        color: "#ffffff",
        weight: 10,
        opacity: 0.82,
        lineCap: "round",
        lineJoin: "round"
      }}).addTo(map);

      flightData.segments.forEach((segment, index) => {{
        const left = flightData.points[index];
        const right = flightData.points[index + 1];
        if (!left || !right) {{
          return;
        }}
        L.polyline([[left.lat, left.lon], [right.lat, right.lon]], {{
          color: segment.color,
          weight: 6,
          opacity: 0.96,
          lineCap: "round",
          lineJoin: "round"
        }}).addTo(map);
      }});

      function marker(point, label, color) {{
        return L.circleMarker([point.lat, point.lon], {{
          radius: 7,
          color: "#ffffff",
          weight: 2,
          fillColor: color,
          fillOpacity: 1,
          opacity: 1
        }}).addTo(map).bindTooltip(label, {{
          permanent: true,
          direction: "top",
          offset: [0, -12],
          className: "gpsMarkerTooltip"
        }});
      }}

      marker(flightData.points[0], "Start", "#27ae60");
      marker(flightData.points[flightData.points.length - 1], "End", "#eb5757");
      renderLegend();

      const bounds = L.latLngBounds(pathLatLngs);
      setTimeout(() => {{
        map.invalidateSize();
        if (bounds.isValid()) {{
          map.fitBounds(bounds, {{
            padding: [24, 24],
            maxZoom: 17
          }});
        }}
      }}, 0);
      return map;
    }}

    function switchToLeaflet(reason) {{
      if (activeMode === "leaflet") {{
        return;
      }}
      activeMode = "leaflet";
      clearReadinessTimer();
      destroyCesiumViewer();
      cesiumContainer.hidden = true;
      leafletContainer.hidden = false;
      setStatus(reason ? "Leaflet fallback: " + reason : "Leaflet fallback", "error");
      try {{
        if (!leafletMap) {{
          leafletMap = buildLeafletMap();
        }} else {{
          setTimeout(() => {{
            if (leafletMap) {{
              leafletMap.invalidateSize();
            }}
          }}, 0);
        }}
      }} catch (error) {{
        const details = error && error.message ? error.message : String(error);
        setStatus("Leaflet render error: " + details, "error");
      }}
    }}

    function initCesium() {{
      try {{
        const osmProvider = new Cesium.OpenStreetMapImageryProvider({{
          url: flightData.osmUrl,
          fileExtension: "png"
        }});

        const viewer = new Cesium.Viewer("cesiumContainer", {{
          animation: false,
          baseLayer: new Cesium.ImageryLayer(osmProvider),
          baseLayerPicker: false,
          fullscreenButton: false,
          geocoder: false,
          infoBox: false,
          navigationHelpButton: true,
          sceneMode: Cesium.SceneMode.SCENE3D,
          sceneModePicker: true,
          selectionIndicator: false,
          shouldAnimate: false,
          timeline: false,
          terrainProvider: new Cesium.EllipsoidTerrainProvider()
        }});
        cesiumViewer = viewer;

        viewer.scene.globe.depthTestAgainstTerrain = false;
        viewer.scene.screenSpaceCameraController.enableCollisionDetection = false;

        osmProvider.errorEvent.addEventListener((tileError) => {{
          if (activeMode !== "cesium") {{
            return;
          }}
          const details = tileError && (tileError.message || (tileError.error && tileError.error.message));
          switchToLeaflet("OSM tile error" + (details ? ": " + details : ""));
        }});

        viewer.scene.globe.tileLoadProgressEvent.addEventListener((remaining) => {{
          if (activeMode !== "cesium") {{
            return;
          }}
          if (remaining > 0) {{
            transientStatus("Loading map tiles... " + remaining, "info", 900);
          }} else {{
            markCesiumReady();
          }}
        }});

        viewer.scene.renderError.addEventListener((scene, error) => {{
          if (activeMode !== "cesium") {{
            return;
          }}
          const details = error && error.message ? error.message : String(error);
          switchToLeaflet("Render error: " + details);
        }});

        readinessTimer = setTimeout(() => {{
          if (activeMode === "cesium" && !cesiumReady) {{
            switchToLeaflet("Cesium readiness timed out");
          }}
        }}, 10000);

        const pathPositions = [];
        flightData.points.forEach((point) => {{
          pathPositions.push(point.lon, point.lat);
        }});
        viewer.entities.add({{
          name: "Flight path underlay",
          polyline: {{
            positions: Cesium.Cartesian3.fromDegreesArray(pathPositions),
            width: 10,
            clampToGround: true,
            material: Cesium.Color.WHITE.withAlpha(0.82)
          }}
        }});

        flightData.segments.forEach((segment, index) => {{
          const positions = Cesium.Cartesian3.fromDegreesArray([
            segment.positions[0],
            segment.positions[1],
            segment.positions[3],
            segment.positions[4]
          ]);
          viewer.entities.add({{
            name: `Flight segment ${{index + 1}}`,
            description: `Rows ${{segment.startRow}}-${{segment.endRow}}`,
            polyline: {{
              positions,
              width: 6,
              clampToGround: true,
              material: Cesium.Color.fromCssColorString(segment.color).withAlpha(0.96)
            }}
          }});
        }});

        function marker(point, label, color) {{
          viewer.entities.add({{
            name: label,
            position: Cesium.Cartesian3.fromDegrees(point.lon, point.lat, point.alt),
            point: {{
              pixelSize: 12,
              color: Cesium.Color.fromCssColorString(color),
              outlineColor: Cesium.Color.WHITE,
              outlineWidth: 2,
              disableDepthTestDistance: Number.POSITIVE_INFINITY
            }},
            label: {{
              text: label,
              font: "13px Arial",
              fillColor: Cesium.Color.WHITE,
              outlineColor: Cesium.Color.BLACK,
              outlineWidth: 3,
              style: Cesium.LabelStyle.FILL_AND_OUTLINE,
              pixelOffset: new Cesium.Cartesian2(0, -24),
              disableDepthTestDistance: Number.POSITIVE_INFINITY
            }}
          }});
        }}

        marker(flightData.points[0], "Start", "#27ae60");
        marker(flightData.points[flightData.points.length - 1], "End", "#eb5757");
        renderLegend();

        const allPositions = flightData.points.map((point) =>
          Cesium.Cartesian3.fromDegrees(point.lon, point.lat, point.alt)
        );
        const sphere = Cesium.BoundingSphere.fromPoints(allPositions);
        const range = Math.max(sphere.radius * 3.2, 500.0);
        viewer.camera.flyToBoundingSphere(sphere, {{
          duration: 0,
          offset: new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-38), range)
        }});
        viewer.scene.requestRender();
        if (!cesiumReady) {{
          transientStatus("Preparing map...", "info", 900);
        }}
      }} catch (error) {{
        const details = error && error.message ? error.message : String(error);
        switchToLeaflet("Cesium error: " + details);
      }}
    }}

    if (preferLeafletRenderer) {{
      activeMode = "leaflet";
      try {{
        leafletMap = buildLeafletMap();
        transientStatus("2D map ready", "ok", 1200);
      }} catch (error) {{
        const details = error && error.message ? error.message : String(error);
        activeMode = "cesium";
        leafletContainer.hidden = true;
        cesiumContainer.hidden = false;
        if (!window.Cesium) {{
          setStatus("Map render error: " + details, "error");
        }} else {{
          initCesium();
        }}
      }}
    }} else if (!window.Cesium) {{
      switchToLeaflet("Cesium script unavailable");
    }} else {{
      initCesium();
    }}
  </script>
</body>
</html>
"""


def figure_html(fig: go.Figure, bridge: bool = False, dark: bool = True) -> str:
    body = fig.to_html(
        include_plotlyjs="cdn",
        full_html=False,
        config={"responsive": True, "scrollZoom": True, "displaylogo": False},
    )
    background = "#1f242b" if dark else "#ffffff"
    if not bridge:
        return f"<html><body style='margin:0;background:{background}'>{body}</body></html>"
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
