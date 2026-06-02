from __future__ import annotations

import json

import pandas as pd
import plotly.graph_objects as go

from .models import LoadedLog
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


def _axis_name(index: int) -> str:
    return "y" if index == 0 else f"y{index + 1}"


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
