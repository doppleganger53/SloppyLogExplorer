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


def test_gps_map_html_uses_cesium_openstreetmap_without_api_keys(tmp_path: Path) -> None:
    path = tmp_path / "flight.csv"
    write_sample(path)
    log = load_log(path)

    html = build_gps_map_html(log, GpsGradientOptions(color_column="Current(A)"))

    assert "Cesium.Viewer" in html
    assert "Cesium.OpenStreetMapImageryProvider" in html
    assert "tile.openstreetmap.org" in html
    assert "OpenStreetMap contributors" in html
    assert "Ion.defaultAccessToken" not in html
    assert "createWorldTerrain" not in html
    assert "createWorldImagery" not in html


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


def test_library_tree_defaults_collapsed_and_keeps_file_date_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app_root = tmp_path / "appdata"
    monkeypatch.setenv("APPDATA", str(app_root))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from sloppy_log_explorer.main_window import MainWindow

    root = tmp_path / "frsky"
    root.mkdir()

    alpha_new = root / "Alpha-2026-01-02-10-00-00.csv"
    alpha_old = root / "Alpha-2026-01-01-10-00-00.csv"
    beta = root / "Beta-2026-01-03-10-00-00.csv"
    write_sample(alpha_new)
    write_sample(alpha_old)
    write_sample(beta)
    os.utime(alpha_new, (2000, 2000))
    os.utime(alpha_old, (1000, 1000))
    os.utime(beta, (1500, 1500))

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.load_library(root)

    header = window.library_tree.header()
    assert window.library_tree.columnCount() == 4
    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.Interactive
    assert header.sectionResizeMode(1) == QHeaderView.ResizeMode.Interactive
    assert header.sectionResizeMode(2) == QHeaderView.ResizeMode.Interactive
    assert header.sectionResizeMode(3) == QHeaderView.ResizeMode.Interactive
    assert header.sortIndicatorSection() == 2
    assert header.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder

    top_level = [window.library_tree.topLevelItem(index) for index in range(window.library_tree.topLevelItemCount())]
    assert all(not item.isExpanded() for item in top_level)

    alpha = next(item for item in top_level if item.text(0) == "Alpha")
    assert alpha.text(2) == window._format_timestamp(2000)
    assert alpha.text(3) == window._format_size(alpha_new.stat().st_size + alpha_old.stat().st_size)
    assert alpha.child(0).text(0) == alpha_new.name
    assert alpha.child(1).text(0) == alpha_old.name

    window.library_header_clicked(0)
    alpha = next(item for item in [window.library_tree.topLevelItem(index) for index in range(window.library_tree.topLevelItemCount())] if item.text(0) == "Alpha")
    assert alpha.child(0).text(0) == alpha_new.name
    assert alpha.child(1).text(0) == alpha_old.name
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
    assert "Cesium.Viewer" in gps_map
