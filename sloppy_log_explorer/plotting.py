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
    primary: LoadedLog,
    columns: list[str],
    compare: LoadedLog | None = None,
    selected_index: int | None = None,
    show_grid: bool = True,
    dark: bool = True,
) -> go.Figure:
    fig = go.Figure()
    x = relative_seconds(primary)
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
                    x=relative_seconds(compare),
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
        "dragmode": "pan",
        "margin": {"l": 58, "r": 64 + max(0, len(columns) - 1) * 44, "t": 30, "b": 46},
        "legend": {"orientation": "h", "y": 1.08, "x": 0},
        "xaxis": {
            "title": "Elapsed time (s)",
            "showgrid": show_grid,
            "gridcolor": "rgba(255,255,255,0.08)" if dark else "rgba(0,0,0,0.1)",
        },
    }
    for idx, col in enumerate(columns):
        axis_key = "yaxis" if idx == 0 else f"yaxis{idx + 1}"
        axis = {
            "title": col,
            "titlefont": {"color": COLORS[idx % len(COLORS)]},
            "tickfont": {"color": COLORS[idx % len(COLORS)]},
            "showgrid": show_grid and idx == 0,
            "gridcolor": "rgba(255,255,255,0.08)" if dark else "rgba(0,0,0,0.1)",
        }
        if idx > 0:
            axis.update({"overlaying": "y", "side": "right", "position": min(0.98, 0.86 + idx * 0.045)})
        layout[axis_key] = axis
    fig.update_layout(**layout)

    if selected_index is not None and x:
        selected_index = max(0, min(selected_index, len(x) - 1))
        fig.add_vline(x=x[selected_index], line_width=2, line_color="#ffffff" if dark else "#1f2937", opacity=0.55)
    return fig


def build_gps_figure(log: LoadedLog, color_column: str | None = None, dark: bool = True) -> go.Figure:
    fig = go.Figure()
    gps = log.gps_columns
    if gps is None:
        fig.update_layout(
            template="plotly_dark" if dark else "plotly",
            annotations=[{"text": "No GPS latitude/longitude columns detected.", "showarrow": False}],
        )
        return fig

    df = log.dataframe
    z = pd.to_numeric(df[gps.altitude], errors="coerce") if gps.altitude else list(range(len(df)))
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
            "xaxis_title": gps.longitude,
            "yaxis_title": gps.latitude,
            "zaxis_title": gps.altitude or "Sample",
        },
    )
    return fig


def figure_html(fig: go.Figure, bridge: bool = False) -> str:
    body = fig.to_html(include_plotlyjs="cdn", full_html=False, config={"responsive": True, "scrollZoom": True})
    if not bridge:
        return f"<html><body style='margin:0;background:#1f242b'>{body}</body></html>"
    return f"""
<html>
<head>
  <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
  <style>html,body{{height:100%;margin:0;background:#1f242b;overflow:hidden}}</style>
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

