"""Regression coverage for user actions found during the release UX audit."""

from pathlib import Path

import pytest
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox, QTableWidgetItem

from sloppy_log_explorer import main_window as ui
from sloppy_log_explorer.plotting import build_telemetry_figure, figure_html


def test_telemetry_chart_explicitly_disables_cloud_upload(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    rendered = figure_html(build_telemetry_figure(None, []))
    assert '"showSendToCloud": false' in rendered
    assert '"modeBarButtonsToRemove": ["sendChartToCloud"]' in rendered


@pytest.fixture
def window(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Discard)
    app = QApplication.instance() or QApplication([])
    view = ui.MainWindow()
    yield view
    view.close()
    app.processEvents()


def _log(path: Path) -> Path:
    path.write_text(
        "Time,VFAS(V),Current(A),GPS Lat,GPS Lon\n"
        "0,16,2,39.0,-75.0\n1,15,5,39.0001,-75.0001\n",
        encoding="utf-8",
    )
    return path


def test_renaming_alias_profile_preserves_draft_rows(window, monkeypatch) -> None:
    window.store.save_aliases("Existing", {"SA": "Gear"})
    window.populate_alias_profiles("Existing")
    window.alias_table.item(0, 1).setText("Landing gear")
    editor = window.alias_profile.lineEdit()
    editor.selectAll()
    QTest.keyClicks(editor, "New profile")
    assert window.alias_table.item(0, 1).text() == "Landing gear"
    window.save_alias_profile()
    assert window.store.load_aliases("New profile") == {"SA": "Landing gear"}
    assert window.store.load_aliases("Existing") == {"SA": "Gear"}


def test_flight_notes_switch_can_cancel_or_save_to_original_log(window, tmp_path, monkeypatch) -> None:
    first = _log(tmp_path / "first.csv")
    second = _log(tmp_path / "second.csv")
    window.load_log(first)
    window.tabs.setCurrentWidget(window.flight_notes_tab)
    window.flight_notes.setPlainText("Check elevator trim")
    window.flight_video.setText("flight.mp4")
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Cancel)
    window.load_log(second)
    assert window.current_log.info.path == first
    assert window.flight_notes.toPlainText() == "Check elevator trim"
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Save)
    window.load_log(second)
    assert window.current_log.info.path == second
    assert window.store.get_flight(str(first))["notes"] == "Check elevator trim"
    assert window.store.get_flight(str(first))["video_path"] == "flight.mp4"
    assert window.flight_notes.toPlainText() == ""
    assert window.store.get_flight(str(second)).get("notes", "") == ""


def test_discarded_flight_notes_do_not_prompt_again_when_editor_is_deferred(window, tmp_path, monkeypatch) -> None:
    window.load_log(_log(tmp_path / "first.csv"))
    window.tabs.setCurrentWidget(window.flight_notes_tab)
    window.flight_notes.setPlainText("Draft")
    window.tabs.setCurrentIndex(0)
    prompts = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *_args: prompts.append(1) or QMessageBox.StandardButton.Discard,
    )
    window.load_log(_log(tmp_path / "second.csv"))
    window.load_log(_log(tmp_path / "third.csv"))
    assert prompts == [1]
    window.tabs.setCurrentWidget(window.flight_notes_tab)
    assert window.flight_notes.toPlainText() == ""


def test_closing_with_unsaved_notes_can_be_cancelled(window, tmp_path, monkeypatch) -> None:
    window.load_log(_log(tmp_path / "flight.csv"))
    window.tabs.setCurrentWidget(window.flight_notes_tab)
    window.flight_notes.setPlainText("Draft")
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Cancel)
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert window.store.get_setting("last_log") is not None
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Discard)


def test_midpoint_toggle_preserves_manual_gps_range(window, tmp_path) -> None:
    window.load_log(_log(tmp_path / "flight.csv"))
    window.gps_color_combo.setCurrentText("VFAS(V)")
    window.gps_auto_range_check.setChecked(False)
    window.gps_min_spin.setValue(10)
    window.gps_max_spin.setValue(20)
    window.gps_midpoint_check.setChecked(True)
    assert window.gps_min_spin.value() == 10
    assert window.gps_max_spin.value() == 20


def test_gps_marker_handles_infinite_sensor_values(window, tmp_path) -> None:
    window.load_log(_log(tmp_path / "flight.csv"))
    window.current_log.dataframe["VFAS(V)"] = [float("inf"), 15.0]
    window.gps_color_combo.setCurrentText("VFAS(V)")
    assert window._gps_marker_values(0) == [{"label": "VFAS(V)", "value": None}]
    assert window.gps_min_spin.value() == 15.0
    assert window.gps_max_spin.value() == 15.0


def test_changing_sync_paths_discards_old_copy_selection(window, tmp_path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _log(source / "flight.csv")
    window.sync_source.setText(str(source))
    window.sync_target.setText(str(tmp_path / "target"))
    window.scan_sync()
    window.sync_table.selectRow(0)
    assert len(window._selected_sync_candidates()) == 1
    window.sync_target.setText(str(tmp_path / "another-target"))
    assert window.sync_candidates == []
    assert window.sync_table.rowCount() == 0
    assert window._selected_sync_candidates() == []


@pytest.mark.parametrize("operation", ["scan_sync", "copy_sync_candidates", "open_voice_csv", "save_voice_csv", "generate_voice_pack"])
def test_external_operation_errors_are_actionable_and_keep_ui_alive(window, tmp_path, monkeypatch, operation) -> None:
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda _parent, title, message: warnings.append((title, message)))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *_args: (str(tmp_path / "missing.csv"), ""))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_args: (str(tmp_path / "missing" / "out.csv"), ""))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *_args: str(tmp_path))
    window.sync_source.setText(str(tmp_path / "missing"))
    window.sync_target.setText(str(tmp_path / "target"))
    if operation == "copy_sync_candidates":
        monkeypatch.setattr(window, "_selected_sync_candidates", lambda: [object()])
        def fail_copy(_candidates):
            raise PermissionError("The target is read-only.")
        monkeypatch.setattr(ui, "copy_candidates", fail_copy)
    if operation == "generate_voice_pack":
        window.voice_table.setRowCount(1)
        window.voice_table.setItem(0, 0, QTableWidgetItem("Armed"))
        window.voice_table.setItem(0, 1, QTableWidgetItem("armed.wav"))
        def fail_voice(_items, _path):
            raise RuntimeError("No local speech engine is available.")
        monkeypatch.setattr(ui, "generate_voice_pack", fail_voice)
    getattr(window, operation)()
    assert len(warnings) == 1
    assert warnings[0][1]
    assert window.isEnabled()
    assert window.store.get_setting("unused") is None


def test_library_load_error_retains_previous_library(window, tmp_path, monkeypatch) -> None:
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: warnings.append(_args))
    window.library_root = tmp_path
    window.load_library(tmp_path / "missing")
    assert window.library_root == tmp_path
    assert len(warnings) == 1


def test_all_tabs_and_normalization_fit_an_800_pixel_window(window) -> None:
    window.show()
    window.reception_normalize_check.setChecked(True)
    for index in range(window.tabs.count()):
        window.tabs.setCurrentIndex(index)
        window.resize(1280, 800)
        QApplication.processEvents()
        assert window.width() == 1280
        assert window.height() == 800


def test_returning_to_flight_map_does_not_reset_camera(window, tmp_path, monkeypatch) -> None:
    window.load_log(_log(tmp_path / "flight.csv"))
    window.tabs.setCurrentWidget(window.gps_tab)
    requested_fits = []
    monkeypatch.setattr(window.gps_view, "refresh_viewport", lambda fit=False: requested_fits.append(fit))
    window.tabs.setCurrentIndex(0)
    window.tabs.setCurrentWidget(window.gps_tab)
    assert requested_fits == [False]


def test_axis_overrides_survive_same_layout_and_unrelated_logs(window, tmp_path) -> None:
    first = _log(tmp_path / "first.csv")
    second = _log(tmp_path / "second.csv")
    different = tmp_path / "different.csv"
    different.write_text("Time,VFAS(V),RPM\n0,15,1000\n1,14,1200\n", encoding="utf-8")
    window.load_log(first)
    window.telemetry_axis_groups = [("VFAS(V)", "Current(A)")]
    window.telemetry_axis_ungrouped_columns = {"GPS Lat", "GPS Lon"}

    for path in (second, different, first):
        window.load_log(path)
        assert window.current_log.info.path == path
        assert window.telemetry_axis_groups == [("VFAS(V)", "Current(A)")]
        assert window.telemetry_axis_ungrouped_columns == {"GPS Lat", "GPS Lon"}
        assert window.graph_view.manual_axis_groups == [("VFAS(V)", "Current(A)")]
        figure = build_telemetry_figure(
            window.current_log, window.current_log.parameter_columns,
            manual_axis_groups=window.telemetry_axis_groups,
            ungrouped_axis_columns=tuple(window.telemetry_axis_ungrouped_columns),
        )
        traces = {trace.name: trace for trace in figure.data}
        if path == different:
            assert set(traces) == {"VFAS(V)", "RPM"}
            assert traces["VFAS(V)"].yaxis != traces["RPM"].yaxis
        else:
            assert traces["VFAS(V)"].yaxis == traces["Current(A)"].yaxis
            assert traces["GPS Lat"].yaxis != traces["GPS Lon"].yaxis


def test_axis_overrides_do_not_persist_between_application_sessions(window, tmp_path) -> None:
    window.load_log(_log(tmp_path / "flight.csv"))
    window.telemetry_axis_groups = [("VFAS(V)", "Current(A)")]
    window.telemetry_axis_ungrouped_columns = {"GPS Lat"}
    restarted = ui.MainWindow()
    try:
        assert restarted.current_log is not None
        assert restarted.telemetry_axis_groups == []
        assert restarted.telemetry_axis_ungrouped_columns == set()
        assert restarted.graph_view.manual_axis_groups == []
    finally:
        restarted.close()
