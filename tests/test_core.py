from __future__ import annotations

from pathlib import Path
import os

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QHeaderView

from sloppy_log_explorer.analysis import calculate_internal_resistance, cursor_values, find_current_columns, find_voltage_columns, suggest_display_columns
from sloppy_log_explorer.library import group_by_model, scan_library
from sloppy_log_explorer.models import GpsGradientOptions
from sloppy_log_explorer.parser import load_log
from sloppy_log_explorer.plotting import build_gps_figure, build_gps_map_html, build_gps_map_payload, build_telemetry_figure, figure_html
from sloppy_log_explorer.qt_plot import GpsPathWidget
from sloppy_log_explorer.sync import copy_candidates, discover_sync_candidates


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


def write_split_coordinate_sample(path: Path) -> None:
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


def write_gps_outlier_sample(path: Path) -> None:
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
    assert "const flightCanvas = document.getElementById(\"flightCanvas\");" in html
    assert "canvasPathReady" in html
    assert "function drawFlightCanvas()" in html
    assert "map.project(coordinate)" in html
    assert "[\"move\", \"zoom\", \"rotate\", \"pitch\", \"resize\", \"render\"]" in html
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


def test_internal_resistance_regression(tmp_path: Path) -> None:
    path = tmp_path / "flight.csv"
    write_sample(path)
    log = load_log(path)

    result = calculate_internal_resistance(log.dataframe, "VFAS(V)", "Current(A)", cells=4)

    assert result is not None
    assert 20 <= result.pack_milliohm <= 40
    assert result.health in {"Good", "Fair"}


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


def test_real_log_validator_accepts_current_3d_map_contract(tmp_path: Path) -> None:
    from tools.validate_real_log import validate_core

    path = tmp_path / "Panther" / "flight.csv"
    path.parent.mkdir()
    write_sample(path)

    result = validate_core(path, None, tmp_path)

    assert result["has_gps_lat_lon"] is True
    assert result["library_logs_scanned"] == 1
