from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from sloppy_log_explorer.analysis import (
    calculate_internal_resistance,
    cursor_values,
    find_current_columns,
    find_voltage_columns,
    suggest_display_columns,
)
from sloppy_log_explorer.library import scan_library
from sloppy_log_explorer.models import GpsGradientOptions
from sloppy_log_explorer.parser import load_log
from sloppy_log_explorer.plotting import build_gps_map_html, build_telemetry_figure, figure_html


def as_jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def validate_core(log_path: Path, compare_path: Path | None, library_root: Path) -> dict[str, Any]:
    log = load_log(log_path, library_root)
    if log.info.rows <= 0:
        raise AssertionError("log has no rows")
    if len(log.parameter_columns) <= 0:
        raise AssertionError("log has no numeric telemetry columns")
    if log.time is None or not log.time.notna().any():
        raise AssertionError("log time column was not detected")

    selected = suggest_display_columns(log.parameter_columns)
    if len(selected) < 2:
        raise AssertionError(f"not enough plottable columns selected: {selected}")

    fig = build_telemetry_figure(log, selected, selected_index=min(10, log.info.rows - 1))
    if len(fig.data) < len(selected):
        raise AssertionError("telemetry figure did not create a trace for each selected column")
    html = figure_html(fig, bridge=True)
    if "plotly" not in html.lower() or "qwebchannel" not in html.lower():
        raise AssertionError("telemetry HTML is missing Plotly or the Qt bridge")

    compare = load_log(compare_path, library_root) if compare_path else log
    common = [column for column in selected if column in compare.dataframe.columns]
    compare_fig = build_telemetry_figure(log, common, compare=compare, selected_index=min(10, log.info.rows - 1))
    if common and len(compare_fig.data) < len(common) * 2:
        raise AssertionError("compare figure did not include primary and compare traces")

    index = min(max(1, log.info.rows // 2), log.info.rows - 1)
    cursor = cursor_values(log, index, common or selected, compare)
    if not cursor:
        raise AssertionError("cursor values were empty")

    gps_html = build_gps_map_html(log, GpsGradientOptions(color_column=selected[0]))
    gps_html_lower = gps_html.lower()
    if log.info.has_gps and ("maplibregl.map" not in gps_html_lower or "tile.openstreetmap.org" not in gps_html_lower):
        raise AssertionError("GPS map HTML is missing MapLibre or OpenStreetMap raster tiles")
    if log.info.has_gps:
        required_map_tokens = [
            "maplibre-gl-csp.js",
            "maplibre-gl-csp-worker.js",
            "maplibregl.workerUrl",
            "new maplibregl.Map",
            "OpenStreetMap contributors",
            "function buildRasterBaseStyle()",
            'id: "osm-raster-base"',
            'const pathParts = Array.isArray(flightData.pathParts)',
            "function buildFlightGeoJson()",
            'id: "flight-underlay"',
            'id: "flight-segments"',
            'id: "flight-extrusions"',
            'type: "fill-extrusion"',
            '"fill-extrusion-height": ["get", "height"]',
            "native3dPathReady: extrusionLayerReady",
            'map.addSource("flight-markers"',
            '"line-color": ["get", "color"]',
            "flightData.segments.forEach((segment, index) => {",
            "let flightLayersAdded = false",
            "flightLayersAdded = true",
            "maxZoom: mapMaxZoom",
            "maxZoom: mapFitMaxZoom",
            "maxPitch: mapMaxPitch",
            "function setCameraMode(mode, options)",
            "webglPathReady: elevationLayerReady",
            "ribbonVertexCount",
            "gl.drawArrays(gl.TRIANGLES, 0, vertexCount)",
            'id="flightCanvas"',
            'id="cameraControls"',
            'data-camera-mode="ground"',
            "canvasPathReady",
            "function drawFlightCanvas()",
            "map.project(coordinate)",
            "const altitudeStats = flightData.altitudeStats || {};",
            "function altitudeRenderMeters(point)",
            "function buildElevationRenderData()",
            "maplibregl.MercatorCoordinate.fromLngLat",
            'id: "flight-elevation-layer"',
            'renderingMode: "3d"',
            "elevationLayerReady",
            "map.dragRotate.enable()",
            "map.fitBounds(flightBounds",
            'map.once("style.load", revealFlightPath)',
            "window.sloppyGpsMap",
            "getState: debugMapState",
            "window.__sloppyDebugMapState = state",
            "document.body.dataset.mapState = state.state",
        ]
        missing = [token for token in required_map_tokens if token not in gps_html]
        if missing:
            raise AssertionError(f"GPS map HTML is missing visible map/path tokens: {missing}")
    voltage_columns = find_voltage_columns(log.parameter_columns)
    current_columns = find_current_columns(log.parameter_columns)
    ir_result = None
    for voltage in voltage_columns:
        for current in current_columns:
            ir_result = calculate_internal_resistance(log.dataframe, voltage, current)
            if ir_result is not None:
                break
        if ir_result is not None:
            break

    library_logs = scan_library(library_root)
    if not any(item.path == log_path for item in library_logs):
        raise AssertionError("library scan did not include the selected log")

    return {
        "log": str(log_path),
        "compare": str(compare.info.path),
        "model": log.info.model,
        "rows": log.info.rows,
        "columns": log.info.columns,
        "duration_seconds": log.info.duration_seconds,
        "has_gps_lat_lon": log.info.has_gps,
        "selected_columns": selected,
        "telemetry_traces": len(fig.data),
        "compare_traces": len(compare_fig.data),
        "cursor_columns": [value.column for value in cursor],
        "voltage_columns_found": voltage_columns[:8],
        "current_columns_found": current_columns[:8],
        "battery_ir": None
        if ir_result is None
        else {
            "pack_milliohm": round(ir_result.pack_milliohm, 3),
            "cell_milliohm": round(ir_result.cell_milliohm, 3),
            "health": ir_result.health,
            "voltage_column": ir_result.voltage_column,
            "current_column": ir_result.current_column,
            "samples": ir_result.samples,
        },
        "library_logs_scanned": len(library_logs),
    }


def validate_ui(
    log_path: Path,
    library_root: Path,
    state_root: Path | None,
    platform: str,
    render_graph: Path | None,
) -> dict[str, Any]:
    if state_root is None:
        state_root = Path(tempfile.mkdtemp(prefix="sloppy-log-explorer-"))
    os.environ["APPDATA"] = str(state_root)
    if platform == "offscreen":
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    else:
        os.environ.pop("QT_QPA_PLATFORM", None)

    from PyQt6.QtWidgets import QApplication

    from sloppy_log_explorer.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.library_root = library_root
    window.load_log(log_path)
    selected = window.selected_columns()
    if not selected:
        raise AssertionError("UI did not select any columns for the loaded log")
    window.set_selected_index(min(5, window.current_log.info.rows - 1))
    if "Cursor row" not in window.info_panel.toHtml():
        raise AssertionError("UI cursor panel did not render loaded log values")
    result = {
        "ui_title": window.windowTitle(),
        "ui_rows": window.current_log.info.rows,
        "ui_selected_columns": selected,
        "ui_state_root": str(state_root),
        "ui_platform": platform,
        "graph_view_type": type(window.graph_view).__name__,
        "gps_view_type": type(window.gps_view).__name__,
    }
    if render_graph is not None:
        render_graph.parent.mkdir(parents=True, exist_ok=True)
        window.graph_view.resize(1200, 650)
        image = window.graph_view.grab()
        if not image.save(str(render_graph)):
            raise AssertionError(f"failed to save graph render to {render_graph}")
        result["render_graph"] = str(render_graph)
    window.close()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Sloppy Log Explorer against a real telemetry log.")
    parser.add_argument("log", type=Path)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--library-root", type=Path, required=True)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--ui-platform", choices=["offscreen", "native"], default="offscreen")
    parser.add_argument("--render-graph", type=Path)
    parser.add_argument("--skip-ui", action="store_true")
    args = parser.parse_args()

    result = validate_core(args.log, args.compare, args.library_root)
    if not args.skip_ui:
        result["ui"] = validate_ui(args.log, args.library_root, args.state_root, args.ui_platform, args.render_graph)
    print(json.dumps(result, indent=2, default=as_jsonable))


if __name__ == "__main__":
    main()
