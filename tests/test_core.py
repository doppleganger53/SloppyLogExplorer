from __future__ import annotations

import math
from pathlib import Path
import os
import sqlite3
from typing import Any, cast

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QHeaderView, QMessageBox, QFileDialog, QTableWidgetItem

from sloppy_log_explorer.analysis import calculate_internal_resistance, cursor_values, find_current_columns, find_voltage_columns, suggest_display_columns
from sloppy_log_explorer.library import group_by_model, scan_library
from sloppy_log_explorer.models import GpsGradientOptions
from sloppy_log_explorer.parser import load_log
from sloppy_log_explorer.plotting import (
    _telemetry_trace_point_budget,
    build_gps_figure,
    build_gps_map_html,
    build_gps_map_payload,
    build_telemetry_figure,
    figure_html,
)
from sloppy_log_explorer.qt_plot import GpsPathWidget, TelemetryPlotWidget
from sloppy_log_explorer.sync import copy_candidates, discover_sync_candidates
from sloppy_log_explorer.voice import VoiceItem, generate_voice_pack


def write_sample(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "Date,Time,VFAS(V),Current(A),GPS Lat,GPS Lon,Alt(m)",
                "2026-01-01,12:00:00,16.80,0.5,39.0,-75.0,10",
                "2026-01-01,12:00:01,16.55,10.0,39.1,-75.1,12",
                "2026-01-01,12:00:02,16.30,20.0,39.2,-75.2,14",
                "2026-01-01,12:00:03,16.05,30.0,39.3,-75.3,16",
                "2026-01-01,12:00:04,15.80,40.0,39.4,-75.4,18",
                "2026-01-01,12:00:05,15.55,50.0,39.5,-75.5,20",
                "2026-01-01,12:00:06,15.30,60.0,39.6,-75.6,22",
                "2026-01-01,12:00:07,15.05,70.0,39.7,-75.7,24",
                "2026-01-01,12:00:08,14.80,80.0,39.8,-75.8,26",
            ]
        ),
        encoding="utf-8",
    )


def write_coordinate_sample(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "Date,Time,GPS",
                '2026-01-01,12:00:00,"39.0000,-75.0000,10"',
                '2026-01-01,12:00:01,"39.0005,-75.0005,11"',
                '2026-01-01,12:00:02,"39.0010,-75.0010,12"',
            ]
        ),
        encoding="utf-8",
    )


def write_coordinate_sample_with_separate_altitude(path: Path) -> None:
    # Exercise the parser path that extracts lat/lon from one field but keeps a
    # separate numeric altitude column for the rendered map.
    path.write_text(
        "\n".join(
            [
                "Date,Time,GPS,GPS alt(m)",
                '2026-01-01,12:00:00,"39.0000,-75.0000",100',
                '2026-01-01,12:00:01,"39.0005,-75.0005",105',
                '2026-01-01,12:00:02,"39.0010,-75.0010",112',
            ]
        ),
        encoding="utf-8",
    )


def write_coordinate_sample_with_missing_altitude(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "Date,Time,GPS,GPS alt(m)",
                '2026-01-01,12:00:00,"39.0000,-75.0000",',
                '2026-01-01,12:00:01,"39.0005,-75.0005",105',
                '2026-01-01,12:00:02,"39.0010,-75.0010",',
            ]
        ),
        encoding="utf-8",
    )


def write_split_coordinate_sample(path: Path) -> None:
    # Nonstandard column names should still be recognized when their numeric
    # ranges clearly match latitude and longitude.
    path.write_text(
        "\n".join(
            [
                "Date,Time,Northing,Easting",
                "2026-01-01,12:00:00,39.0000,-75.0000",
                "2026-01-01,12:00:01,39.0005,-75.0005",
                "2026-01-01,12:00:02,39.0010,-75.0010",
            ]
        ),
        encoding="utf-8",
    )


def write_cardinal_coordinate_sample(path: Path) -> None:
    # Hemisphere suffixes/prefixes show up in some exports, so this sample
    # checks that the parser understands signed cardinal notation.
    path.write_text(
        "\n".join(
            [
                "Date,Time,Position",
                '2026-01-01,12:00:00,"39.0000N 75.0000W"',
                '2026-01-01,12:00:01,"39.0005 N, 75.0005 W"',
                '2026-01-01,12:00:02,"N39.0010 W75.0010"',
            ]
        ),
        encoding="utf-8",
    )


def write_origin_placeholder_sample(path: Path) -> None:
    # Some logs start or end with zeroed coordinates that should be ignored
    # instead of drawing a bogus jump from the Gulf of Guinea.
    path.write_text(
        "\n".join(
            [
                "Date,Time,GPS Lat,GPS Lon,Alt(m)",
                "2026-01-01,12:00:00,0,0,10",
                "2026-01-01,12:00:01,39.0000,-75.0000,11",
                "2026-01-01,12:00:02,39.0005,-75.0005,12",
                "2026-01-01,12:00:03,0,0,13",
            ]
        ),
        encoding="utf-8",
    )


def write_origin_only_coordinate_sample(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "Date,Time,GPS",
                '2026-01-01,12:00:00,"0,0,10"',
                '2026-01-01,12:00:01,"0,0,12"',
                '2026-01-01,12:00:02,"0,0,14"',
            ]
        ),
        encoding="utf-8",
    )


def write_gps_outlier_sample(path: Path) -> None:
    # This sample forces the outlier-pruning logic to discard isolated far-away
    # points without breaking the normal flight path into extra parts.
    path.write_text(
        "\n".join(
            [
                "Date,Time,GPS Lat,GPS Lon,Alt(m)",
                "2026-01-01,12:00:00,5.0000,-10.0000,10",
                "2026-01-01,12:00:01,39.0000,-75.0000,11",
                "2026-01-01,12:00:02,39.0005,-75.0005,12",
                "2026-01-01,12:00:03,4.0000,-12.0000,13",
                "2026-01-01,12:00:04,39.0010,-75.0010,14",
                "2026-01-01,12:00:05,39.0015,-75.0015,15",
                "2026-01-01,12:00:06,6.0000,-11.0000,16",
            ]
        ),
        encoding="utf-8",
    )


def write_no_gps_sample(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "Date,Time,VFAS(V),Current(A),Throttle",
                "2026-01-01,12:00:00,16.80,0.5,0",
                "2026-01-01,12:00:01,16.55,10.0,20",
                "2026-01-01,12:00:02,16.30,20.0,40",
                "2026-01-01,12:00:03,16.05,30.0,60",
                "2026-01-01,12:00:04,15.80,40.0,80",
            ]
        ),
        encoding="utf-8",
    )


def write_gps_course_only_sample(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "Date,Time,TxBat(V),Pot1,GPS course(°),Current(A),Altitude(m)",
                "2026-01-01,12:00:00,7.6,12,359.8,0.2,10",
                "2026-01-01,12:00:01,7.5,13,359.9,0.3,11",
                "2026-01-01,12:00:02,7.4,14,359.9,0.1,12",
            ]
        ),
        encoding="utf-8",
    )


def write_library_blob(path: Path, size: int) -> None:
    path.write_text("x" * size, encoding="utf-8")


def test_load_log_detects_time_numeric_and_gps(tmp_path: Path) -> None:
    path = tmp_path / "Panther" / "flight.csv"
    path.parent.mkdir()
    write_sample(path)

    log = load_log(path, tmp_path)

    assert log.info.model == "Panther"
    assert log.info.rows == 9
    assert log.info.has_gps is True
    assert "VFAS(V)" in log.parameter_columns
    assert log.info.duration_seconds == 8


def test_load_log_detects_coordinate_string_gps_under_single_heading(tmp_path: Path) -> None:
    path = tmp_path / "string_gps.csv"
    write_coordinate_sample(path)

    log = load_log(path)

    assert log.info.has_gps is True
    assert log.gps_columns is not None
    assert log.gps_columns.latitude_label == "GPS (lat)"
    assert log.gps_columns.longitude_label == "GPS (lon)"
    assert log.gps_columns.altitude_label == "GPS (alt)"

    fig = build_gps_figure(log)
    trace = fig.data[0]

    assert list(trace.x[:2]) == pytest.approx([-75.0, -75.0005])
    assert list(trace.y[:2]) == pytest.approx([39.0, 39.0005])
    assert fig.layout.scene.xaxis.title.text == "GPS (lon)"
    assert fig.layout.scene.yaxis.title.text == "GPS (lat)"
    assert fig.layout.scene.zaxis.title.text == "GPS (alt)"


def test_load_log_pairs_coordinate_string_gps_with_separate_gps_altitude(tmp_path: Path) -> None:
    path = tmp_path / "string_gps_external_alt.csv"
    write_coordinate_sample_with_separate_altitude(path)

    log = load_log(path)

    assert log.info.has_gps is True
    assert log.gps_columns is not None
    assert log.gps_columns.latitude_label == "GPS (lat)"
    assert log.gps_columns.longitude_label == "GPS (lon)"
    assert log.gps_columns.altitude == "GPS alt(m)"
    assert log.gps_columns.altitude_label == "GPS alt(m)"

    payload = build_gps_map_payload(log)
    assert payload["altitudeLabel"] == "GPS alt(m)"
    assert [point["alt"] for point in payload["points"][:3]] == pytest.approx([100.0, 105.0, 112.0])


def test_gps_map_payload_serializes_missing_altitude_as_zero(tmp_path: Path) -> None:
    path = tmp_path / "string_gps_missing_alt.csv"
    write_coordinate_sample_with_missing_altitude(path)

    log = load_log(path)
    payload = build_gps_map_payload(log)
    html = build_gps_map_html(log)

    assert payload["status"] == "ok"
    assert [point["alt"] for point in payload["points"]] == pytest.approx([0.0, 105.0, 0.0])
    assert "maplibregl.Map" in html


def test_load_log_detects_split_coordinate_columns_with_arbitrary_headings(tmp_path: Path) -> None:
    path = tmp_path / "split_gps.csv"
    write_split_coordinate_sample(path)

    log = load_log(path)

    assert log.info.has_gps is True
    assert log.gps_columns is not None
    assert log.gps_columns.latitude == "Northing"
    assert log.gps_columns.longitude == "Easting"
    assert log.gps_columns.latitude_label == "Northing"
    assert log.gps_columns.longitude_label == "Easting"

    gps = build_gps_figure(log)
    trace = gps.data[0]

    assert list(trace.x[:2]) == pytest.approx([-75.0, -75.0005])
    assert list(trace.y[:2]) == pytest.approx([39.0, 39.0005])
    assert gps.layout.scene.xaxis.title.text == "Easting"
    assert gps.layout.scene.yaxis.title.text == "Northing"


def test_load_log_does_not_invent_gps_from_gps_course_only(tmp_path: Path) -> None:
    path = tmp_path / "gps_course_only.csv"
    write_gps_course_only_sample(path)

    log = load_log(path)

    assert log.gps_columns is None
    assert log.info.has_gps is False


def test_load_log_detects_cardinal_decimal_coordinate_strings(tmp_path: Path) -> None:
    path = tmp_path / "cardinal_gps.csv"
    write_cardinal_coordinate_sample(path)

    log = load_log(path)

    assert log.info.has_gps is True
    assert log.gps_columns is not None
    assert list(log.dataframe[log.gps_columns.latitude].head(2)) == pytest.approx([39.0, 39.0005])
    assert list(log.dataframe[log.gps_columns.longitude].head(2)) == pytest.approx([-75.0, -75.0005])


def test_gps_map_html_uses_maplibre_openstreetmap_without_api_keys(tmp_path: Path) -> None:
    path = tmp_path / "flight.csv"
    write_sample(path)
    log = load_log(path)

    html = build_gps_map_html(log, GpsGradientOptions(color_column="Current(A)"))

    assert "maplibregl.Map" in html
    assert "maplibre-gl-csp.js" in html
    assert "maplibre-gl-csp-worker.js" in html
    assert "maplibregl.workerUrl" in html
    assert "https://tile.openstreetmap.org/{z}/{x}/{y}.png" in html
    assert "OpenStreetMap contributors" in html
    forbidden = ["".join(parts) for parts in [
        ("Ces", "ium"),
        ("Leaf", "let"),
        ("leaf", "let"),
        ("Ion.", "defaultAccessToken"),
        ("createWorld", "Terrain"),
        ("createWorld", "Imagery"),
    ]]
    assert [token for token in forbidden if token in html] == []


def test_gps_map_html_renders_path_underlay_and_segment_overlays(tmp_path: Path) -> None:
    path = tmp_path / "flight.csv"
    write_sample(path)
    log = load_log(path)

    payload = build_gps_map_payload(log, GpsGradientOptions(color_column="Current(A)"))
    html = build_gps_map_html(log, GpsGradientOptions(color_column="Current(A)"))

    # This is a contract test for the generated payload and HTML template, so
    # it checks both data shape and the renderer tokens the runtime validator
    # depends on.
    assert payload["status"] == "ok"
    assert len(payload["points"]) == 9
    assert len(payload["segments"]) == len(payload["points"]) - 1
    assert payload["altitudeLabel"] == "Alt(m)"
    assert payload["altitudeStats"] == {
        "label": "Alt(m)",
        "minimum": 10.0,
        "maximum": 26.0,
        "baseMeters": 10.0,
    }
    assert payload["altitudeScale"] == 5.0
    assert payload["altitudeFloorMeters"] == 12.0
    assert payload["mapMaxZoom"] == 19
    assert payload["mapFitMaxZoom"] == 17
    assert payload["mapMaxPitch"] == 85
    assert payload["rasterTileMaxZoom"] == 19
    assert payload["rasterTileUrls"] == ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"]
    assert payload["timeline"] == {
        "enabled": True,
        "durationSeconds": 8.0,
        "startElapsedSeconds": 0.0,
        "endElapsedSeconds": 8.0,
        "rows": 9,
    }
    assert payload["points"][0]["elapsedSeconds"] == pytest.approx(0.0)
    assert payload["points"][-1]["elapsedSeconds"] == pytest.approx(8.0)
    assert 'const pathParts = Array.isArray(flightData.pathParts)' in html
    assert "function buildFlightGeoJson()" in html
    assert "pathParts.forEach((part, index) => {" in html
    assert "coordinatesFromPart(part)" in html
    assert 'id: "flight-underlay"' in html
    assert 'source: "flight-underlay-source"' in html
    assert '"line-color": "#111827"' in html
    assert '"line-opacity": 0.16' in html
    assert "flightData.segments.forEach((segment, index) => {" in html
    assert "const left = coordinateFromPoint(segment.left);" in html
    assert "const right = coordinateFromPoint(segment.right);" in html
    assert 'id="flightCanvas"' in html
    assert 'id="currentPointReadout"' in html
    assert 'id="playbackOverlay"' in html
    assert 'id="playbackSlider"' in html
    assert 'id="playbackSpeed"' in html
    assert 'value="10"' in html
    assert "qrc:///qtwebchannel/qwebchannel.js" in html
    assert "let gpsBridge = null" in html
    assert "function connectGpsBridge()" in html
    assert "function interpolatedPointForElapsed(elapsedSeconds)" in html
    assert "function drawCurrentCursorMarker()" in html
    assert "function setCursor(cursor)" in html
    assert "function setPlayback(playback)" in html
    assert "const flightCanvas = document.getElementById(\"flightCanvas\");" in html
    assert "canvasPathReady" in html
    assert "function drawFlightCanvas()" in html
    assert "function scheduleFlightCanvasDraw()" in html
    assert "map.project(coordinate)" in html
    assert "[\"move\", \"zoom\", \"rotate\", \"pitch\", \"resize\"]" in html
    assert "[\"move\", \"zoom\", \"rotate\", \"pitch\", \"resize\", \"render\"]" not in html
    assert "const altitudeStats = flightData.altitudeStats || {};" in html
    assert "function altitudeRenderMeters(point)" in html
    assert "function buildElevationRenderData()" in html
    assert "function matrixForUniform(renderArgs)" in html
    assert "renderArgs.modelViewProjectionMatrix" in html
    assert "maplibregl.MercatorCoordinate.fromLngLat" in html
    assert "function mercatorCoordinate(point, altitudeMeters)" in html
    assert "function setCameraMode(mode, options)" in html
    assert "addRibbonQuad(ribbonPositions, ribbonColors" in html
    assert "gl.drawArrays(gl.TRIANGLES, 0, vertexCount)" in html
    assert "gl.disable(gl.DEPTH_TEST)" in html
    assert 'id: "flight-elevation-layer"' in html
    assert 'id: "flight-extrusions"' in html
    assert 'type: "fill-extrusion"' in html
    assert '"fill-extrusion-height": ["get", "height"]' in html
    assert 'renderingMode: "3d"' in html
    assert "extrusionLayerReady" in html
    assert "native3dPathReady: extrusionLayerReady" in html
    assert "elevationLayerReady" in html
    assert "webglPathReady: elevationLayerReady" in html
    assert "ribbonVertexCount" in html
    assert "window.__sloppyDebugMapState = state" in html
    assert "document.body.dataset.mapState = state.state" in html
    assert "function refreshMapViewport(options)" in html
    assert 'id="cameraControls"' in html
    assert 'data-camera-mode="ground"' in html
    assert 'id: "osm-raster-base"' in html
    assert '"https://tile.openstreetmap.org/{z}/{x}/{y}.png"' in html
    assert 'id: "flight-segments"' in html
    assert 'map.addSource("flight-extrusions-source"' in html
    assert '"line-color": ["get", "color"]' in html
    assert '"line-opacity": 0.22' in html
    assert 'map.addSource("flight-markers"' in html
    assert 'id: "flight-marker-circles"' in html
    assert 'id: "flight-marker-labels"' in html
    assert "let flightLayersAdded = false" in html
    assert "if (flightLayersAdded)" in html
    assert "flightLayersAdded = true" in html
    assert "const mapMaxZoom = Number(flightData.mapMaxZoom) || 19;" in html
    assert "const mapFitMaxZoom = Number(flightData.mapFitMaxZoom) || Math.min(17, mapMaxZoom);" in html
    assert "const mapMaxPitch = Number(flightData.mapMaxPitch) || 85;" in html
    assert "maxZoom: mapMaxZoom" in html
    assert "maxZoom: mapFitMaxZoom" in html
    assert "map.fitBounds(flightBounds" in html
    assert 'map.once("style.load", revealFlightPath)' in html
    assert 'map.once("load", revealFlightPath)' in html
    assert "maxPitch: mapMaxPitch" in html
    assert "pitch: Math.min(54, mapMaxPitch)" in html
    assert "requestedMode === \"ground\" ? Math.min(84, mapMaxPitch)" in html
    assert "bearing: initialBearing" in html
    assert "map.dragRotate.enable()" in html
    assert "map.touchZoomRotate.enableRotation()" in html
    assert "window.sloppyGpsMap" in html
    assert "fit: fitFlightBounds" in html
    assert "refresh: refreshMapViewport" in html
    assert "setCameraMode" in html
    assert "setCursor" in html
    assert "setPlayback" in html
    assert "getState: debugMapState" in html
    assert 'id="flightOverlay"' not in html
    assert "function drawFlightOverlay()" not in html
    assert "function addRasterFallbackLayer()" not in html
    assert "map.dragRotate.disable()" not in html


def test_gps_map_payload_skips_origin_placeholder_points(tmp_path: Path) -> None:
    path = tmp_path / "flight.csv"
    write_origin_placeholder_sample(path)

    log = load_log(path)
    payload = build_gps_map_payload(log)
    build_gps_map_html(log)

    assert payload["status"] == "ok"
    assert len(payload["points"]) == 2
    assert len(payload["pathParts"]) == 1
    assert len(payload["segments"]) == 1
    assert payload["points"][0]["lat"] == pytest.approx(39.0)
    assert payload["points"][0]["lon"] == pytest.approx(-75.0)
    assert payload["points"][-1]["lat"] == pytest.approx(39.0005)
    assert payload["points"][-1]["lon"] == pytest.approx(-75.0005)


def test_load_log_does_not_report_gps_for_origin_only_coordinate_strings(tmp_path: Path) -> None:
    path = tmp_path / "origin_only.csv"
    write_origin_only_coordinate_sample(path)

    log = load_log(path)

    assert log.info.has_gps is False
    assert log.gps_columns is None


def test_gps_map_payload_skips_isolated_far_away_points(tmp_path: Path) -> None:
    path = tmp_path / "flight.csv"
    write_gps_outlier_sample(path)

    log = load_log(path)
    payload = build_gps_map_payload(log)

    assert payload["status"] == "ok"
    assert [point["row"] for point in payload["points"]] == [2, 3, 5, 6]
    assert len(payload["pathParts"]) == 1
    assert len(payload["segments"]) == 3
    assert payload["points"][0]["lat"] == pytest.approx(39.0)
    assert payload["points"][0]["lon"] == pytest.approx(-75.0)
    assert payload["points"][-1]["lat"] == pytest.approx(39.0015)
    assert payload["points"][-1]["lon"] == pytest.approx(-75.0015)


def test_gps_webengine_widget_loads_map_from_local_html_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "flight.csv"
    write_sample(path)
    log = load_log(path)

    class FakeView:
        def __init__(self) -> None:
            self.html: str | None = None
            self.url = None

        def setHtml(self, html: str) -> None:
            self.html = html

        def setUrl(self, url) -> None:
            self.url = url

    fake_view = FakeView()
    fake_widget = GpsPathWidget.__new__(GpsPathWidget)
    fake_widget._web_engine = True
    fake_widget._view = fake_view
    previous_path = tmp_path / "old-gps-map.html"
    previous_path.write_text("old", encoding="utf-8")
    fake_widget._html_path = previous_path

    monkeypatch.setattr("sloppy_log_explorer.qt_plot.app_data_dir", lambda: tmp_path)

    GpsPathWidget.set_path(fake_widget, log)

    assert fake_view.html is None
    assert fake_view.url is not None
    assert not previous_path.exists()
    html_path = Path(fake_view.url.toLocalFile())
    assert html_path.exists()
    html = html_path.read_text(encoding="utf-8")
    assert "maplibregl.Map" in html
    assert "initialMapMode" not in html


def test_telemetry_webengine_widget_loads_plot_from_local_html_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "flight.csv"
    write_sample(path)
    log = load_log(path)

    class FakeView:
        def __init__(self) -> None:
            self.html: str | None = None
            self.url = None

        def setHtml(self, html: str) -> None:
            self.html = html

        def setUrl(self, url) -> None:
            self.url = url

    fake_view = FakeView()
    fake_widget = type("FakeTelemetryWidget", (), {})()
    fake_widget._web_engine = True
    fake_widget._view = fake_view
    fake_widget.log = log
    fake_widget.compare = None
    fake_widget.columns = ["VFAS(V)"]
    fake_widget.selected_index = 0
    fake_widget.show_grid = True
    fake_widget.dark = True
    fake_widget.interaction_mode = "pan"
    fake_widget.time_mode = "absolute"
    previous_path = tmp_path / "old-telemetry-plot.html"
    previous_path.write_text("old", encoding="utf-8")
    fake_widget._html_path = previous_path
    fake_widget._current_trace_point_budget = lambda: TelemetryPlotWidget._current_trace_point_budget(fake_widget)

    monkeypatch.setattr("sloppy_log_explorer.qt_plot.app_data_dir", lambda: tmp_path)

    TelemetryPlotWidget._render(fake_widget)

    assert fake_view.html is None
    assert fake_view.url is not None
    assert not previous_path.exists()
    html_path = Path(fake_view.url.toLocalFile())
    assert html_path.exists()
    html = html_path.read_text(encoding="utf-8")
    assert "plotly-" in html
    assert "cdn.plot.ly" not in html
    assert "QWebChannel" in html


def test_telemetry_widget_passes_viewport_scaled_trace_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "flight.csv"
    write_sample(path)
    log = load_log(path)

    class FakeView:
        def __init__(self, width: int) -> None:
            self._width = width
            self.html: str | None = None

        def width(self) -> int:
            return self._width

        def setHtml(self, html: str) -> None:
            self.html = html

    import plotly.graph_objects as go

    budgets: list[int] = []

    def fake_build_telemetry_figure(*_args, **kwargs):
        budgets.append(kwargs["max_trace_points"])
        return go.Figure()

    monkeypatch.setattr("sloppy_log_explorer.qt_plot.build_telemetry_figure", fake_build_telemetry_figure)
    monkeypatch.setattr("sloppy_log_explorer.qt_plot.figure_html", lambda *_args, **_kwargs: "<html></html>")

    fake_view = FakeView(width=700)
    fake_widget = type("FakeTelemetryWidget", (), {})()
    fake_widget._web_engine = False
    fake_widget._view = fake_view
    fake_widget.log = log
    fake_widget.compare = None
    fake_widget.columns = ["VFAS(V)"]
    fake_widget.selected_index = 0
    fake_widget.show_grid = True
    fake_widget.dark = True
    fake_widget.interaction_mode = "pan"
    fake_widget.time_mode = "absolute"
    fake_widget._html_path = None
    fake_widget._current_trace_point_budget = lambda: TelemetryPlotWidget._current_trace_point_budget(fake_widget)

    TelemetryPlotWidget._render(fake_widget)
    fake_view._width = 4000
    TelemetryPlotWidget._render(fake_widget)

    assert budgets == [2312, 12000]
    assert fake_view.html == "<html></html>"


def test_telemetry_widget_clamps_narrow_trace_budget_before_budgeting() -> None:
    class FakeView:
        def width(self) -> int:
            return 100

    fake_widget = cast(Any, type("FakeTelemetryWidget", (), {})())
    fake_widget._view = FakeView()
    fake_widget.columns = [f"Sensor {index}" for index in range(24)]

    assert TelemetryPlotWidget._current_trace_point_budget(fake_widget) == 2000


def test_telemetry_widget_unknown_width_uses_hard_cap_trace_budget() -> None:
    class FakeView:
        def width(self) -> int:
            raise RuntimeError("width not available")

    fake_widget = cast(Any, type("FakeTelemetryWidget", (), {})())
    fake_widget._view = FakeView()
    fake_widget.columns = ["VFAS(V)"]

    assert TelemetryPlotWidget._current_trace_point_budget(fake_widget) == 12000


def test_gps_webengine_widget_refreshes_map_viewport() -> None:
    class FakePage:
        def __init__(self) -> None:
            self.scripts: list[str] = []

        def runJavaScript(self, script: str, callback=None) -> None:
            self.scripts.append(script)
            if callback:
                callback(True)

    fake_widget = GpsPathWidget.__new__(GpsPathWidget)
    fake_widget._web_engine = True
    fake_widget._page = FakePage()

    GpsPathWidget.refresh_viewport(fake_widget, fit=True)

    assert fake_widget._page.scripts
    assert "window.sloppyGpsMap.refresh" in fake_widget._page.scripts[-1]
    assert '"fit": true' in fake_widget._page.scripts[-1]


def test_gps_webengine_widget_updates_cursor_without_reloading_map() -> None:
    class FakePage:
        def __init__(self) -> None:
            self.scripts: list[str] = []

        def runJavaScript(self, script: str, callback=None) -> None:
            self.scripts.append(script)
            if callback:
                callback(True)

    fake_widget = GpsPathWidget.__new__(GpsPathWidget)
    fake_widget._web_engine = True
    fake_widget._page = FakePage()
    fake_widget._last_cursor = None

    GpsPathWidget.set_cursor(
        fake_widget,
        {
            "index": 3,
            "row": 4,
            "elapsedSeconds": 3.0,
            "durationSeconds": 8.0,
            "playing": False,
            "speed": 1.0,
            "values": [{"label": "Current(A)", "value": 30.0}],
        },
    )

    assert fake_widget._last_cursor["row"] == 4
    assert fake_widget._page.scripts
    assert "window.sloppyGpsMap ? window.sloppyGpsMap.setCursor" in fake_widget._page.scripts[-1]
    assert '"elapsedSeconds": 3.0' in fake_widget._page.scripts[-1]


def test_gps_map_payload_colors_segments_with_manual_gradient(tmp_path: Path) -> None:
    path = tmp_path / "flight.csv"
    write_sample(path)
    log = load_log(path)

    payload = build_gps_map_payload(
        log,
        GpsGradientOptions(
            color_column="Current(A)",
            start_color="#000000",
            end_color="#ffffff",
            auto_range=False,
            range_min=0,
            range_max=80,
        ),
    )
    reverse_payload = build_gps_map_payload(
        log,
        GpsGradientOptions(
            color_column="Current(A)",
            start_color="#000000",
            end_color="#ffffff",
            reverse=True,
            auto_range=False,
            range_min=0,
            range_max=80,
        ),
    )

    assert payload["status"] == "ok"
    assert len(payload["points"]) == 9
    assert len(payload["segments"]) == 8
    assert payload["legend"]["enabled"] is True
    assert payload["segments"][0]["color"] == "#111111"
    assert payload["segments"][-1]["color"] == "#efefef"
    assert reverse_payload["segments"][0]["color"] == "#eeeeee"
    assert reverse_payload["segments"][-1]["color"] == "#101010"


def test_load_log_does_not_invent_gps_from_regular_telemetry(tmp_path: Path) -> None:
    path = tmp_path / "no_gps.csv"
    write_no_gps_sample(path)

    log = load_log(path)

    assert log.info.has_gps is False
    assert log.gps_columns is None
    assert "No GPS latitude/longitude columns detected" in build_gps_map_html(log)


def test_load_log_skips_coordinate_text_scan_for_numeric_non_gps_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sloppy_log_explorer.parser as parser_module

    path = tmp_path / "numeric_no_gps.csv"
    headers = ["Sample"] + [f"Sensor{i}" for i in range(20)]
    rows = [",".join(headers)]
    for row in range(100):
        rows.append(",".join(str(row + column) for column in range(len(headers))))
    path.write_text("\n".join(rows), encoding="utf-8")

    calls = 0
    real_parser = parser_module._parse_coordinate_text

    def count_coordinate_parse(text: str):
        nonlocal calls
        calls += 1
        return real_parser(text)

    monkeypatch.setattr(parser_module, "_parse_coordinate_text", count_coordinate_parse)

    log = load_log(path)

    assert log.info.has_gps is False
    assert calls == 0


def test_load_log_detects_late_fix_coordinate_text_column(tmp_path: Path) -> None:
    path = tmp_path / "late_gps.csv"
    rows = ["Time,GPS,Voltage"]
    for index in range(250):
        rows.append(f"{index},No fix,{16.8 - index * 0.001:.3f}")
    rows.extend(
        [
            "250,\"39.774389,-75.204944,20\",16.5",
            "251,\"39.774450,-75.204900,21\",16.4",
        ]
    )
    path.write_text("\n".join(rows), encoding="utf-8")

    log = load_log(path)

    assert log.info.has_gps is True
    assert log.gps_columns is not None
    assert log.gps_columns.latitude_label == "GPS (lat)"


def test_load_log_detects_mixed_numeric_placeholder_coordinate_text_column(tmp_path: Path) -> None:
    path = tmp_path / "mixed_gps.csv"
    rows = ["Time,GPS,Voltage"]
    for index in range(250):
        rows.append(f"{index},0,{16.8 - index * 0.001:.3f}")
    rows.extend(
        [
            "250,\"39.774389,-75.204944,20\",16.5",
            "251,\"39.774450,-75.204900,21\",16.4",
        ]
    )
    path.write_text("\n".join(rows), encoding="utf-8")

    log = load_log(path)

    assert log.info.has_gps is True
    assert log.gps_columns is not None
    assert log.gps_columns.latitude_label == "GPS (lat)"
    assert log.gps_columns.longitude_label == "GPS (lon)"
    assert log.gps_columns.altitude_label == "GPS (alt)"


def test_load_log_detects_late_fix_coordinate_text_column_without_name_hint(tmp_path: Path) -> None:
    path = tmp_path / "late_gps_status.csv"
    rows = ["Time,Status,Voltage"]
    for index in range(250):
        rows.append(f"{index},No fix,{16.8 - index * 0.001:.3f}")
    rows.extend(
        [
            "250,\"39.774389,-75.204944,20\",16.5",
            "251,\"39.774450,-75.204900,21\",16.4",
        ]
    )
    path.write_text("\n".join(rows), encoding="utf-8")

    log = load_log(path)

    assert log.info.has_gps is True
    assert log.gps_columns is not None
    assert log.gps_columns.latitude_label == "Status (lat)"
    assert log.gps_columns.longitude_label == "Status (lon)"
    assert log.gps_columns.altitude_label == "Status (alt)"


def test_internal_resistance_regression(tmp_path: Path) -> None:
    path = tmp_path / "flight.csv"
    write_sample(path)
    log = load_log(path)

    result = calculate_internal_resistance(log.dataframe, "VFAS(V)", "Current(A)", cells=4)

    assert result is not None
    assert 20 <= result.pack_milliohm <= 40
    assert result.health in {"Good", "Fair"}


def test_app_store_migrates_older_flight_table_with_no_video_path(tmp_path: Path) -> None:
    from sloppy_log_explorer.storage import AppStore

    db_path = tmp_path / "state.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.execute("create table flights (file_path text primary key, model text, notes text default '')")
    conn.commit()
    conn.close()

    store = AppStore(db_path)
    store.save_flight("flight.csv", "Panther", "good flight", "clip.mp4")
    flight = store.get_flight("flight.csv")
    store.close()

    assert flight["notes"] == "good flight"
    assert flight["video_path"] == "clip.mp4"


def test_battery_ir_uses_selected_battery_cell_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app_root = tmp_path / "appdata"
    monkeypatch.setenv("APPDATA", str(app_root))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: None)

    from sloppy_log_explorer.main_window import MainWindow

    path = tmp_path / "flight.csv"
    write_sample(path)

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.load_log(path)
    battery_id = window.store.add_battery("Six cell pack", 6)
    window.refresh_batteries()
    for index in range(window.battery_select.count()):
        data = window.battery_select.itemData(index)
        if isinstance(data, dict) and data["id"] == battery_id:
            window.battery_select.setCurrentIndex(index)
            break

    window.calculate_battery_ir()

    history = window.store.list_battery_history()
    assert len(history) == 1
    assert history[0]["cell_milliohm"] == pytest.approx(history[0]["pack_milliohm"] / 6)

    window.close()
    app.quit()


def test_battery_ir_warns_when_voltage_or_current_columns_are_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_root = tmp_path / "appdata"
    monkeypatch.setenv("APPDATA", str(app_root))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from sloppy_log_explorer.main_window import MainWindow

    path = tmp_path / "throttle.csv"
    path.write_text(
        "\n".join(
            [
                "Date,Time,Throttle",
                "2026-01-01,12:00:00,0",
                "2026-01-01,12:00:01,25",
                "2026-01-01,12:00:02,50",
            ]
        ),
        encoding="utf-8",
    )
    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, _title, message: warnings.append(message))

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.load_log(path)
    window.store.add_battery("Pack", 4)
    window.refresh_batteries()

    window.calculate_battery_ir()

    assert warnings == ["Select valid voltage and current columns before calculating battery IR."]
    assert window.store.list_battery_history() == []

    window.close()
    app.quit()


def test_cursor_values_include_compare_delta(tmp_path: Path) -> None:
    first = tmp_path / "first.csv"
    second = tmp_path / "second.csv"
    write_sample(first)
    write_sample(second)
    primary = load_log(first)
    compare = load_log(second)
    compare.dataframe["VFAS(V)"] = compare.dataframe["VFAS(V)"] - 0.1

    values = cursor_values(primary, 2, ["VFAS(V)"], compare)

    assert values[0].delta == pytest.approx(0.1)


def test_sync_candidates_copy_newer_logs(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    write_sample(source / "flight.csv")

    candidates = discover_sync_candidates(source, target)
    copied = copy_candidates(candidates)

    assert copied == 1
    assert (target / "flight.csv").exists()


def test_sync_ui_copies_only_selected_candidates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app_root = tmp_path / "appdata"
    monkeypatch.setenv("APPDATA", str(app_root))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from sloppy_log_explorer.main_window import MainWindow

    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    write_sample(source / "first.csv")
    write_sample(source / "second.csv")

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.sync_source.setText(str(source))
    window.sync_target.setText(str(target))
    window.scan_sync()

    assert window.sync_table.rowCount() == 2
    window.sync_table.selectRow(0)
    selected = window._selected_sync_candidates()
    assert len(selected) == 1
    copied_target = selected[0].target
    skipped_targets = [candidate.target for candidate in window.sync_candidates if candidate.target != copied_target]

    window.copy_sync_candidates()

    assert copied_target.exists()
    assert skipped_targets
    assert all(not path.exists() for path in skipped_targets)

    window.close()
    app.quit()


def test_voice_pack_rejects_path_escape_filenames(tmp_path: Path) -> None:
    output = tmp_path / "voice"

    for filename in ("..\\escaped", "folder\\clip", "folder/clip"):
        with pytest.raises(ValueError, match="simple file names"):
            generate_voice_pack([VoiceItem("hello", filename)], output)

    assert not (tmp_path / "escaped.wav").exists()
    assert not (output / "folder\\clip.wav").exists()
    assert not (output / "clip.wav").exists()


def test_generate_voice_pack_gui_shows_warning_for_path_like_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_root = tmp_path / "appdata"
    monkeypatch.setenv("APPDATA", str(app_root))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from sloppy_log_explorer.main_window import MainWindow

    output = tmp_path / "voice"
    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, _title, message: warnings.append(message))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args, **_kwargs: str(output))

    app = QApplication.instance() or QApplication([])
    window = MainWindow()

    window.voice_table.setRowCount(1)
    window.voice_table.setItem(0, 0, QTableWidgetItem("armed"))
    window.voice_table.setItem(0, 1, QTableWidgetItem("../armed"))

    window.generate_voice_pack()

    assert len(warnings) == 1
    assert "simple file names" in warnings[0]
    window.close()
    app.quit()


def test_load_log_infers_model_from_root_level_filename(tmp_path: Path) -> None:
    path = tmp_path / "ERATIX-2025-09-28-18-38-14.csv"
    write_sample(path)

    log = load_log(path, tmp_path)

    assert log.info.model == "ERATIX"


def test_scan_library_uses_metadata_only_and_groups_root_level_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "frsky"
    root.mkdir()
    first = root / "ERATIX-2025-09-28-18-38-14.csv"
    second = root / "5inch-2025-09-29-14-56-25.log"
    ignored = root / "notes.txt"
    write_sample(first)
    write_sample(second)
    ignored.write_text("ignore me", encoding="utf-8")

    def fail_open(*_args, **_kwargs) -> None:
        raise AssertionError("scan_library should not open file contents")

    monkeypatch.setattr("builtins.open", fail_open)

    logs = scan_library(root)

    assert [log.name for log in logs] == [second.name, first.name]
    assert [log.model for log in logs] == ["5inch", "ERATIX"]
    assert all(log.size == log.path.stat().st_size for log in logs)
    assert list(group_by_model(logs)) == ["5inch", "ERATIX"]


def test_library_tree_sorts_top_level_and_children_by_clicked_column_and_preserves_expansion_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app_root = tmp_path / "appdata"
    monkeypatch.setenv("APPDATA", str(app_root))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from sloppy_log_explorer.main_window import MainWindow

    root = tmp_path / "frsky"
    root.mkdir()

    alpha_a = root / "Alpha" / "alpha.csv"
    alpha_b = root / "Alpha" / "omega.csv"
    alpha_c = root / "Alpha" / "zeta.csv"
    beta_a = root / "Beta" / "beta-a.csv"
    beta_b = root / "Beta" / "beta-b.csv"
    gamma = root / "Gamma" / "gamma.csv"
    for path, size in [
        (alpha_a, 300),
        (alpha_b, 100),
        (alpha_c, 200),
        (beta_a, 100),
        (beta_b, 200),
        (gamma, 500),
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_library_blob(path, size)
    os.utime(alpha_a, (1000, 1000))
    os.utime(alpha_b, (3000, 3000))
    os.utime(alpha_c, (2000, 2000))
    os.utime(beta_a, (4000, 4000))
    os.utime(beta_b, (2500, 2500))
    os.utime(gamma, (3500, 3500))

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.load_library(root)

    def top_level_items() -> list:
        return [window.library_tree.topLevelItem(index) for index in range(window.library_tree.topLevelItemCount())]

    def top_level_names() -> list[str]:
        return [item.text(0) for item in top_level_items()]

    def child_names(model: str) -> list[str]:
        item = next(item for item in top_level_items() if item.text(0) == model)
        return [item.child(index).text(0) for index in range(item.childCount())]

    header = window.library_tree.header()
    assert window.library_tree.columnCount() == 4
    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.Interactive
    assert header.sectionResizeMode(1) == QHeaderView.ResizeMode.Interactive
    assert header.sectionResizeMode(2) == QHeaderView.ResizeMode.Interactive
    assert header.sectionResizeMode(3) == QHeaderView.ResizeMode.Interactive
    assert header.sortIndicatorSection() == 2
    assert header.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder

    assert top_level_names() == ["Beta", "Gamma", "Alpha"]
    assert all(not item.isExpanded() for item in top_level_items())

    alpha = next(item for item in top_level_items() if item.text(0) == "Alpha")
    beta = next(item for item in top_level_items() if item.text(0) == "Beta")
    gamma = next(item for item in top_level_items() if item.text(0) == "Gamma")
    assert alpha.text(2) == window._format_timestamp(3000)
    assert alpha.text(3) == window._format_size(alpha_a.stat().st_size + alpha_b.stat().st_size + alpha_c.stat().st_size)
    assert child_names("Alpha") == [alpha_b.name, alpha_c.name, alpha_a.name]
    alpha.setExpanded(True)
    gamma.setExpanded(True)

    window.library_header_clicked(0)
    assert top_level_names() == ["Alpha", "Beta", "Gamma"]
    assert child_names("Alpha") == [alpha_a.name, alpha_b.name, alpha_c.name]
    assert alpha.isExpanded()
    assert not beta.isExpanded()
    assert gamma.isExpanded()

    window.library_header_clicked(3)
    assert top_level_names() == ["Alpha", "Gamma", "Beta"]
    assert child_names("Alpha") == [alpha_a.name, alpha_c.name, alpha_b.name]
    assert alpha.isExpanded()
    assert not beta.isExpanded()
    assert gamma.isExpanded()

    window.library_header_clicked(2)
    assert top_level_names() == ["Beta", "Gamma", "Alpha"]
    assert child_names("Alpha") == [alpha_b.name, alpha_c.name, alpha_a.name]
    assert alpha.isExpanded()
    assert not beta.isExpanded()
    assert gamma.isExpanded()
    window.close()
    app.quit()


def test_gps_color_selector_uses_all_numeric_parameters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app_root = tmp_path / "appdata"
    monkeypatch.setenv("APPDATA", str(app_root))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from sloppy_log_explorer.main_window import MainWindow

    path = tmp_path / "flight.csv"
    write_sample(path)

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.load_log(path)
    window.selected_parameter_columns = {"VFAS(V)"}
    window.populate_columns()
    window.populate_gps_color_combo()

    gps_choices = [window.gps_color_combo.itemText(index) for index in range(window.gps_color_combo.count())]

    assert "VFAS(V)" in gps_choices
    assert "Current(A)" in gps_choices
    assert "Alt(m)" in gps_choices
    assert "Current(A)" not in window.selected_columns()
    window.close()
    app.quit()


def test_gps_marker_value_selectors_drive_cursor_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_root = tmp_path / "appdata"
    monkeypatch.setenv("APPDATA", str(app_root))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from sloppy_log_explorer.main_window import MainWindow

    path = tmp_path / "flight.csv"
    write_sample(path)

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.load_log(path)

    cursor_payloads: list[dict[str, object]] = []
    monkeypatch.setattr(window.gps_view, "set_cursor", lambda payload: cursor_payloads.append(dict(payload)))

    window.gps_color_combo.setCurrentText("Current(A)")
    window.gps_marker_value_combos[0].setCurrentText("Alt(m)")
    window.gps_marker_value_combos[1].setCurrentText("Current(A)")
    map_reloads: list[object] = []
    monkeypatch.setattr(window.gps_view, "set_path", lambda *args, **kwargs: map_reloads.append((args, kwargs)))
    window.set_selected_index(3)

    assert cursor_payloads
    payload = cursor_payloads[-1]
    assert payload["row"] == 4
    assert payload["elapsedSeconds"] == pytest.approx(3.0)
    assert payload["durationSeconds"] == pytest.approx(8.0)
    assert payload["playing"] is False
    assert payload["speed"] == pytest.approx(1.0)
    assert payload["values"] == [
        {"label": "Current(A)", "value": 30.0},
        {"label": "Alt(m)", "value": 16},
    ]
    assert map_reloads == []

    window.close()
    app.quit()


def test_cursor_selection_updates_graph_cursor_without_full_plot_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_root = tmp_path / "appdata"
    monkeypatch.setenv("APPDATA", str(app_root))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from sloppy_log_explorer.main_window import MainWindow

    path = tmp_path / "flight.csv"
    write_sample(path)

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.load_log(path)

    graph_refreshes: list[object] = []
    cursor_updates: list[int] = []
    monkeypatch.setattr(window, "refresh_graph", lambda: graph_refreshes.append(True))
    monkeypatch.setattr(window.graph_view, "set_cursor_index", lambda index: cursor_updates.append(index))

    window.set_selected_index(3)

    assert window.selected_index == 3
    assert cursor_updates == [3]
    assert graph_refreshes == []

    window.close()
    app.quit()


def test_gps_playback_tick_advances_synced_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_root = tmp_path / "appdata"
    monkeypatch.setenv("APPDATA", str(app_root))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    import sloppy_log_explorer.main_window as main_window_module
    from sloppy_log_explorer.main_window import MainWindow

    path = tmp_path / "flight.csv"
    write_sample(path)

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.load_log(path)

    cursor_payloads: list[dict[str, object]] = []
    monkeypatch.setattr(window.gps_view, "set_cursor", lambda payload: cursor_payloads.append(dict(payload)))
    ticks = iter([100.0, 100.3, 100.6])
    monkeypatch.setattr(main_window_module.time, "perf_counter", lambda: next(ticks))

    window.set_gps_playback_speed(1.0)
    window.set_gps_playback_playing(True)
    window.gps_playback_tick()
    assert window.selected_index == 0
    window.gps_playback_tick()

    assert window.selected_index == 1
    assert cursor_payloads[-1]["row"] == 2
    assert cursor_payloads[-1]["elapsedSeconds"] == pytest.approx(0.6)
    assert cursor_payloads[-1]["playing"] is True
    assert cursor_payloads[-1]["speed"] == pytest.approx(1.0)

    window.set_gps_playback_playing(False)
    window.close()
    app.quit()


def test_gps_nearest_index_handles_non_monotonic_timeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_root = tmp_path / "appdata"
    monkeypatch.setenv("APPDATA", str(app_root))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from sloppy_log_explorer.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.gps_timeline_seconds = [0.0, 10.0, 5.0]
    window.gps_timeline_is_monotonic = False

    assert window._nearest_gps_index(6.0) == 2
    assert window._nearest_gps_index(9.0) == 1

    window.close()
    app.quit()


def test_telemetry_selection_does_not_refresh_gps_map_for_gps_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_root = tmp_path / "appdata"
    monkeypatch.setenv("APPDATA", str(app_root))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from sloppy_log_explorer.main_window import MainWindow

    path = tmp_path / "flight.csv"
    write_sample(path)

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.load_log(path)

    gps_refreshes: list[object] = []
    graph_refreshes: list[list[str]] = []

    def record_gps_refresh(*args, **kwargs) -> None:
        gps_refreshes.append((args, kwargs))

    def record_graph_refresh(log, columns, **kwargs) -> None:
        graph_refreshes.append(list(columns))

    monkeypatch.setattr(window.gps_view, "set_path", record_gps_refresh)
    monkeypatch.setattr(window.graph_view, "set_plot", record_graph_refresh)

    item = next(
        window.column_table.item(row, 0)
        for row in range(window.column_table.rowCount())
        if window.column_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == "VFAS(V)"
    )
    item.setCheckState(Qt.CheckState.Unchecked)

    assert "VFAS(V)" not in window.selected_columns()
    assert gps_refreshes == []
    assert graph_refreshes
    assert all("VFAS(V)" not in columns for columns in graph_refreshes)

    window.close()
    app.quit()


def test_gps_tab_has_no_map_mode_selector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app_root = tmp_path / "appdata"
    monkeypatch.setenv("APPDATA", str(app_root))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from sloppy_log_explorer.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()

    assert not hasattr(window, "gps_map_mode")
    assert not hasattr(window, "gps_3d_button")
    assert not hasattr(window, "gps_2d_button")
    assert not hasattr(window, "set_gps_map_mode")
    assert not hasattr(window.gps_view, "map_mode")
    assert not hasattr(window.gps_view, "set_mode")

    window.refresh_gps()

    window.close()
    app.quit()


def test_dark_theme_toggle_updates_app_shell_stylesheet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app_root = tmp_path / "appdata"
    monkeypatch.setenv("APPDATA", str(app_root))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from sloppy_log_explorer.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    dark_stylesheet = window.styleSheet()

    window.dark_action.setChecked(False)
    window.toggle_theme()

    assert window.dark_mode is False
    assert window.styleSheet() != dark_stylesheet
    assert "#f6f8fb" in window.styleSheet()

    window.close()
    app.quit()


def test_saving_alias_profile_keeps_saved_profile_selected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app_root = tmp_path / "appdata"
    monkeypatch.setenv("APPDATA", str(app_root))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from sloppy_log_explorer.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.store.save_aliases("Alpha", {"SA": "Gear"})
    window.populate_alias_profiles("Alpha")
    window.alias_profile.setEditText("Zed")

    window.save_alias_profile()

    assert window.alias_profile.currentText() == "Zed"

    window.close()
    app.quit()


def test_real_frsky_sensor_ranking_prefers_pack_voltage_and_current() -> None:
    columns = [
        "TxBat(V)",
        "1 cell(V)",
        "TRUE Current(A)",
        "Flight Consum.(mAh)",
        "Wiring Temp(°F)",
        "VFAS(V)",
        "RxBatt(V)",
        "BEC voltage(V)",
        "BEC current(A)",
        "SRV1 curr(A)",
        "SRV1 volt(V)",
        "Current(A)",
        "VFR 2.4G(%)",
        "RSSI 2.4G(dB)",
    ]

    assert find_voltage_columns(columns)[:3] == ["VFAS(V)", "1 cell(V)", "RxBatt(V)"]
    assert "BEC current(A)" not in find_voltage_columns(columns)
    assert find_current_columns(columns)[:2] == ["TRUE Current(A)", "Current(A)"]
    assert suggest_display_columns(columns)[:5] == [
        "VFAS(V)",
        "TRUE Current(A)",
        "Current(A)",
        "RxBatt(V)",
        "BEC voltage(V)",
    ]


def test_ragged_frsky_rows_are_loaded(tmp_path: Path) -> None:
    path = tmp_path / "frsky.csv"
    path.write_text(
        "\n".join(
            [
                "Date,Time,RX,RX,VFAS(V),",
                "2026-01-01,12:00:00,1,2,16.8,",
                "2026-01-01,12:00:01,1,2,16.7,,",
                "2026-01-01,12:00:02,1,2,16.6",
            ]
        ),
        encoding="utf-8",
    )

    log = load_log(path)

    assert log.info.rows == 3
    assert "RX" in log.dataframe.columns
    assert "RX.1" in log.dataframe.columns
    assert "VFAS(V)" in log.parameter_columns
    assert log.info.duration_seconds == 2


def test_plotting_supports_current_plotly_axis_schema(tmp_path: Path) -> None:
    path = tmp_path / "flight.csv"
    write_sample(path)
    log = load_log(path)

    fig = build_telemetry_figure(log, ["VFAS(V)", "Current(A)"], selected_index=1)
    html = figure_html(fig, bridge=True)
    gps = build_gps_figure(log, color_column="VFAS(V)")
    gps_map = build_gps_map_html(log, GpsGradientOptions(color_column="VFAS(V)"))

    assert len(fig.data) == 2
    assert fig.layout.yaxis.title.text == "VFAS(V)"
    assert fig.layout.yaxis2.title.text == "Current(A)"
    assert "QWebChannel" in html
    assert len(gps.data) == 1
    assert "maplibregl.Map" in gps_map


def test_telemetry_html_uses_local_plotly_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    fig = build_telemetry_figure(None, [])
    html = figure_html(fig, bridge=True)

    assert "cdn.plot.ly" not in html
    assert "plotly-" in html
    assert "qrc:///qtwebchannel/qwebchannel.js" in html


def test_telemetry_trace_point_budget_scales_with_plot_width() -> None:
    assert _telemetry_trace_point_budget(None) == 12000
    assert _telemetry_trace_point_budget(0) == 12000
    assert _telemetry_trace_point_budget(100) == 2000
    assert _telemetry_trace_point_budget(750) == 3000
    assert _telemetry_trace_point_budget(5000) == 12000


def test_large_telemetry_figure_downsamples_and_preserves_source_indexes(tmp_path: Path) -> None:
    path = tmp_path / "large.csv"
    rows = ["Time,VFAS(V)"]
    for index in range(13050):
        rows.append(f"{index},{16.8 - index * 0.0001:.4f}")
    path.write_text("\n".join(rows), encoding="utf-8")
    log = load_log(path)

    fig = build_telemetry_figure(log, ["VFAS(V)"])
    trace = fig.data[0]

    assert len(trace.x) <= 12001
    assert list(trace.customdata[:2]) == [0, 2]
    assert trace.customdata[-1] == 13049


def test_large_telemetry_downsampling_preserves_spikes_and_dropouts(tmp_path: Path) -> None:
    path = tmp_path / "large_spike.csv"
    rows = ["Time,VFAS(V),Current(A)"]
    for index in range(13050):
        voltage = 16.0
        current = 20.0
        if index == 12001:
            voltage = 4.2
        if index == 12003:
            current = 130.0
        rows.append(f"{index},{voltage},{current}")
    path.write_text("\n".join(rows), encoding="utf-8")
    log = load_log(path)

    fig = build_telemetry_figure(log, ["VFAS(V)", "Current(A)"], max_trace_points=2000)
    voltage_trace = fig.data[0]
    current_trace = fig.data[1]

    assert 12001 in voltage_trace.customdata
    assert 4.2 in voltage_trace.y
    assert 12003 in current_trace.customdata
    assert 130.0 in current_trace.y
    assert len(voltage_trace.x) <= 2000
    assert len(current_trace.x) <= 2000


def test_large_telemetry_downsampling_preserves_nan_gaps(tmp_path: Path) -> None:
    path = tmp_path / "large_gap.csv"
    rows = ["Time,VFAS(V)"]
    for index in range(13050):
        voltage = "" if index == 12002 else "16.0"
        rows.append(f"{index},{voltage}")
    path.write_text("\n".join(rows), encoding="utf-8")
    log = load_log(path)

    fig = build_telemetry_figure(log, ["VFAS(V)"], max_trace_points=2000)
    trace = fig.data[0]
    source_indexes = list(trace.customdata)
    assert 12002 in source_indexes
    gap_position = source_indexes.index(12002)

    assert math.isnan(float(trace.y[gap_position]))
    assert len(trace.x) <= 2000


def test_many_selected_telemetry_columns_keep_plot_readable(tmp_path: Path) -> None:
    path = tmp_path / "many_columns.csv"
    columns = [f"C{index}" for index in range(40)]
    rows = ["Time," + ",".join(columns)]
    for row_index in range(5):
        rows.append(f"{row_index}," + ",".join(str(row_index + column_index) for column_index in range(40)))
    path.write_text("\n".join(rows), encoding="utf-8")
    log = load_log(path)

    fig = build_telemetry_figure(log, columns)
    annotation = fig.layout.annotations[0]

    assert len(fig.data) == 24
    assert fig.layout.margin.r == 284
    assert fig.layout.yaxis6.showticklabels is True
    assert fig.layout.yaxis7.showticklabels is False
    assert fig.data[11].showlegend is True
    assert fig.data[12].showlegend is False
    assert "+ 28 more selected" in annotation.text
    assert "C19: min" not in annotation.text
    assert "Showing first 24 of 40 selected" in fig.layout.annotations[1].text


def test_real_log_validator_accepts_current_3d_map_contract(tmp_path: Path) -> None:
    from tools.validate_real_log import validate_core

    path = tmp_path / "Panther" / "flight.csv"
    path.parent.mkdir()
    write_sample(path)

    result = validate_core(path, None, tmp_path)

    assert result["has_gps_lat_lon"] is True
    assert result["library_logs_scanned"] == 1
