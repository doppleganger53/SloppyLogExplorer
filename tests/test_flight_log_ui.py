from __future__ import annotations

import csv
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import QApplication, QComboBox, QDialog, QFileDialog, QInputDialog, QMenu, QMessageBox

from sloppy_log_explorer.flight_log_sheets import SheetsSyncReport, Workbook, stable_flight_id
from sloppy_log_explorer.flight_log_widget import FlightLogWidget, GoogleSignInSetupDialog, _review_text
from sloppy_log_explorer.flight_logs import FlightLogService
from sloppy_log_explorer.models import LibraryLogInfo
from sloppy_log_explorer.storage import AppStore


def _telemetry(path: Path, *, uncertain: bool = False, day: int = 1) -> LibraryLogInfo:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = ["Date", "Time", "Rx VFR(%)", "Throttle", "Rudder", "Elevator", "Aileron", "Current(A)", "Temp2(°C)"]
    if not uncertain:
        headers += ["GPS speed(mph)", "GPS alt(m)"]
    start = datetime(2026, 7, day, 9)
    begin, end = (30, 270) if uncertain else (60, 180)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        for second in range(end + 80):
            active = begin <= second <= end
            phase = second % 20
            stamp = start + timedelta(seconds=second)
            progress = (second - begin) / (end - begin)
            vfr = 100 - 70 * min(progress, 1 - progress) if uncertain else 80 + phase
            row = [stamp.strftime("%Y-%m-%d"), stamp.strftime("%H:%M:%S"), vfr if active else 0,
                   200 if active else -1024, -500 + phase * 50 if active else 0,
                   400 - phase * 45 if active else 0, -450 + phase * 48 if active else 0,
                   10 + phase if active else 1000, 40 + phase if active else 500]
            if not uncertain:
                row += [35 if active else 0, 50 + phase * 2 if active else 0]
            writer.writerow(row)
    stat = path.stat()
    return LibraryLogInfo(path, "archive", path.name, stat.st_mtime, stat.st_size)


def _settle(app: QApplication, widget: FlightLogWidget, timeout: float = 6) -> None:
    deadline = time.monotonic() + timeout
    while widget._busy or widget._tasks:
        app.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError("Flight-log worker did not finish")
        time.sleep(0.002)
    app.processEvents()


@pytest.fixture
def ui(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    assert isinstance(app, QApplication)
    errors: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *args: errors.append(text))
    monkeypatch.setattr(QMessageBox, "information", lambda *args: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    store = AppStore(tmp_path / "state.sqlite3")
    store.set_setting("flight_logs.output_dir", str(tmp_path / "output"))
    widget = FlightLogWidget(store)
    _settle(app, widget)
    yield app, widget, store, errors
    _settle(app, widget)
    assert widget.shutdown()
    widget.deleteLater()
    app.processEvents()
    store.close()


def test_aircraft_preview_pending_decisions_generation_and_restart(ui, tmp_path: Path) -> None:
    app, widget, store, errors = ui
    root = tmp_path / "library"
    confirmed = _telemetry(root / "archive" / "Falcon-2026-07-01-09-00-00.csv")
    uncertain = _telemetry(root / "archive" / "Glider-2026-07-01-09-00-00.csv", uncertain=True)
    widget.set_library(root, [confirmed, uncertain])
    assert widget.selected_aircraft() == ["Falcon", "Glider"]
    widget.preview_selected()
    _settle(app, widget)
    assert widget._preview is not None
    assert widget._preview.pending_candidates
    assert not widget.generate_button.isEnabled()
    assert not widget.output_dir.exists()
    row = next(index for index, candidate in enumerate(widget._preview.candidates) if candidate["aircraft"] == "Glider")
    combo = widget.candidate_table.cellWidget(row, 5)
    assert isinstance(combo, QComboBox)
    combo.setCurrentText("Accept")
    _settle(app, widget)
    assert widget.generate_button.isEnabled()
    widget.generate_logs()
    _settle(app, widget)
    assert widget.service.list_aircraft() == ["Falcon", "Glider"]
    assert widget._preview is None
    assert not errors
    replacement = FlightLogWidget(store)
    _settle(app, replacement)
    replacement.set_library(root, [confirmed, uncertain])
    replacement.preview_selected()
    _settle(app, replacement)
    assert replacement._preview is not None
    assert not replacement._preview.pending_candidates
    assert replacement._preview.report["duplicates"] == 2
    replacement._select_aircraft(False)
    assert not replacement.preview_button.isEnabled()
    assert store.get_setting("flight_logs.aircraft_selection") == []
    assert replacement.shutdown()
    replacement.deleteLater()


def test_dragging_preview_columns_saves_layout_and_log_drag_preserves_notes(ui, tmp_path: Path) -> None:
    app, widget, store, errors = ui
    source = _telemetry(tmp_path / "library" / "Falcon-2026-07-01.csv")
    widget.set_library(tmp_path / "library", [source])
    widget.preview_selected()
    _settle(app, widget)
    assert widget._preview is not None
    header = widget.output_preview_table.horizontalHeader()
    assert header is not None and header.sectionsMovable()
    header.moveSection(header.count() - 1, 0)
    assert widget._preview.updates[0]["headers"][0] == "Notes"
    widget.generate_logs()
    _settle(app, widget)
    assert widget.tabs.currentIndex() == 1
    assert widget.service.read_log("Falcon").headers[0] == "Notes"
    assert store.get_setting("flight_logs.output_dir") == str(widget.output_dir)
    widget.notes_edit.setPlainText("Pilot | notes in the first column")
    widget.save_notes()
    _settle(app, widget)
    header = widget.log_table.horizontalHeader()
    assert header is not None
    header.moveSection(0, header.count() - 1)
    assert widget.save_columns_button.isEnabled()
    widget.save_column_order()
    _settle(app, widget)
    document = widget.service.read_log("Falcon")
    assert document.headers[-1] == "Notes"
    assert document.rows[0][-1] == "Pilot | notes in the first column"
    assert widget.settings["column_orders"]["Falcon"] == document.headers
    widget.preview_rebuild()
    _settle(app, widget)
    assert widget._preview is not None and widget._preview.updates[0]["headers"] == document.headers
    assert not widget.save_columns_button.isEnabled()
    widget.preview_selected()
    _settle(app, widget)
    assert widget._preview is not None and widget._preview.report["duplicates"] == 1
    assert not errors


def test_bulk_candidate_review_only_changes_explicitly_selected_flights(ui, tmp_path: Path) -> None:
    app, widget, _, errors = ui
    root = tmp_path / "library"
    sources = [_telemetry(root / f"Glider-2026-07-0{day}.csv", uncertain=True, day=day) for day in (1, 2)]
    widget.set_library(root, sources)
    widget.preview_selected()
    _settle(app, widget)
    assert widget._preview is not None and len(widget._preview.pending_candidates) == 2
    assert widget.pending_only_check.isChecked()
    widget.candidate_table.selectRow(0)
    widget._decide_selected("Accept")
    assert not widget._busy
    assert widget._preview is not None and len(widget._preview.pending_candidates) == 1
    assert not widget.generate_button.isEnabled()
    assert not widget.output_dir.exists()
    widget._decide_selected("Reject")
    assert widget._preview is not None and not widget._preview.pending_candidates
    assert widget.generate_button.isEnabled()
    widget.generate_logs()
    _settle(app, widget)
    assert len(widget.service.read_log("Glider").rows) == 1
    assert not errors


def test_maintained_row_context_menu_requests_only_the_chosen_matching_source(ui, tmp_path: Path, monkeypatch) -> None:
    app, widget, _, _ = ui
    root = tmp_path / "library"
    first, second = [_telemetry(root / folder / "Falcon-2026-07-01.csv") for folder in ("a", "b")]
    widget.set_library(root, [first, second])
    widget.preview_button.click()
    _settle(app, widget)
    widget.generate_button.click()
    _settle(app, widget)
    assert widget.log_table.rowCount() == 1
    requested = []
    widget.telemetry_requested.connect(lambda *args: requested.append(args))
    position = widget.log_table.visualRect(widget.log_table.model().index(0, 0)).center()
    monkeypatch.setattr(QMenu, "exec", lambda menu, *_args: menu.actions()[0])
    monkeypatch.setattr(QInputDialog, "getItem", lambda *_args: ("", False))
    widget.log_table.customContextMenuRequested.emit(position)
    _settle(app, widget)
    assert not requested
    monkeypatch.setattr(QInputDialog, "getItem", lambda *_args: (str(second.path), True))
    widget.log_table.customContextMenuRequested.emit(position)
    _settle(app, widget)
    assert requested == [(str(second.path), "2026-07-01 09:01:00", "2026-07-01 09:03:00")]
    first.path.unlink()
    second.path.unlink()
    widget.log_table.customContextMenuRequested.emit(position)
    _settle(app, widget)
    assert len(requested) == 1
    assert "No matching telemetry" in widget.status_label.text()


def test_google_setup_wizard_accepts_desktop_client_and_rejects_service_account(ui, tmp_path: Path, monkeypatch) -> None:
    import json
    _app, widget, _store, errors = ui
    path = tmp_path / "client.json"
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    dialog = GoogleSignInSetupDialog(widget)
    path.write_text(json.dumps({"type": "service_account", "token_uri": "https://oauth2.googleapis.com/token"}))
    dialog._choose_client()
    assert not dialog.client_path_edit.text()
    assert errors and "Desktop app" in errors[-1]
    path.write_text(json.dumps({"installed": {"client_id": "app.apps.googleusercontent.com", "client_secret": "test-client",
                                              "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                                              "token_uri": "https://oauth2.googleapis.com/token"}}))
    dialog._choose_client()
    assert dialog.client_path_edit.text() == str(path)
    assert len(dialog.pageIds()) == 5
    assert widget.credentials_panel.isHidden()
    assert widget.sign_in_button.text() == "Set up Google sign-in…"
    dialog.deleteLater()


def test_notes_save_uses_displayed_flight_number_and_guards_unsaved_close(ui, tmp_path: Path, monkeypatch) -> None:
    app, widget, _, errors = ui
    source = _telemetry(tmp_path / "Falcon-2026-07-01.csv")
    widget.service.generate(widget.service.preview({str(source.path): "Falcon"}))
    widget._refresh_log_list()
    _settle(app, widget)
    assert widget._document is not None
    assert widget._document.rows[0][0] == "1"
    widget.notes_edit.setPlainText("Pilot comment | landed smoothly")
    assert not widget.prepare_close()
    widget.save_notes()
    _settle(app, widget)
    assert widget.service.read_log("Falcon").rows[0][-1] == "Pilot comment | landed smoothly"
    assert widget.prepare_close()
    assert not errors


def test_default_and_aircraft_telemetry_policy_persist_and_inherit(ui, tmp_path: Path) -> None:
    app, widget, _, errors = ui
    source = _telemetry(tmp_path / "library" / "Falcon-2026-07-01.csv")
    widget.set_library(tmp_path / "library", [source])
    assert widget.auto_servo_current_check.isChecked()
    widget._discover_sensors()
    _settle(app, widget)
    headers = [widget.sensor_table.item(row, 0).text() for row in range(widget.sensor_table.rowCount())]
    assert "Current(A)" in headers
    current_row = headers.index("Current(A)")
    widget.sensor_table.item(current_row, 1).setCheckState(Qt.CheckState.Checked)
    widget.save_telemetry_settings()
    _settle(app, widget)
    assert widget.service.load_settings()["defaults"]["max_sensors"] == ["Current(A)"]
    widget.policy_aircraft_combo.setCurrentText("Falcon")
    assert widget.inherit_check.isChecked()
    assert not widget.sensor_table.isEnabled()
    widget.inherit_check.setChecked(False)
    widget.auto_temperature_check.setChecked(False)
    widget.auto_servo_current_check.setChecked(False)
    widget.save_telemetry_settings()
    _settle(app, widget)
    assert not widget.service.load_settings()["planes"]["Falcon"]["auto_temperatures"]
    assert not widget.service.load_settings()["planes"]["Falcon"]["auto_servo_currents"]
    widget.inherit_check.setChecked(True)
    widget.save_telemetry_settings()
    _settle(app, widget)
    assert not widget.service.load_settings()["planes"].get("Falcon")
    assert widget.auto_servo_current_check.isChecked()
    assert not errors


def test_rebuild_requires_separate_review_and_keeps_notes_until_approval(ui, tmp_path: Path, monkeypatch) -> None:
    app, widget, _, _ = ui
    source = _telemetry(tmp_path / "library" / "Falcon-2026-07-01.csv")
    widget.set_library(tmp_path / "library", [source])
    widget.service.generate(widget.service.preview({str(source.path): "Falcon"}))
    widget.service.save_notes("Falcon", 1, "Keep these pilot Notes")
    before = widget.service.read_log("Falcon").path.read_bytes()
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    widget.preview_rebuild()
    _settle(app, widget)
    assert widget._preview is not None and widget._preview.rebuild
    assert widget.service.read_log("Falcon").path.read_bytes() == before
    assert "Schema changes" in widget.preview_details.toPlainText()
    assert "Keep these pilot Notes" in widget.preview_details.toPlainText()
    assert "candidate_id" not in widget.preview_details.toPlainText()


def test_background_scan_stays_responsive_and_discards_changed_library(ui, tmp_path: Path, monkeypatch) -> None:
    app, widget, _, _ = ui
    source = _telemetry(tmp_path / "library" / "Falcon-2026-07-01.csv")
    widget.set_library(tmp_path / "library", [source])
    started, release = threading.Event(), threading.Event()
    preview = widget.service.preview
    main_thread = threading.get_ident()
    worker_threads: list[int] = []

    def slow(*args, **kwargs):
        worker_threads.append(threading.get_ident())
        started.set()
        assert release.wait(3)
        return preview(*args, **kwargs)

    monkeypatch.setattr(widget.service, "preview", slow)
    widget.preview_selected()
    assert started.wait(1)
    assert widget._busy
    assert not widget.prepare_close()
    heartbeat: list[bool] = []
    QTimer.singleShot(0, lambda: heartbeat.append(True))
    app.processEvents()
    assert heartbeat
    widget.set_library(tmp_path / "other", [])
    release.set()
    _settle(app, widget)
    assert widget._preview is None
    assert worker_threads != [main_thread]
    assert "changed" in widget.status_label.text().lower()


def test_sheets_create_sync_and_explicit_remote_notes_import(ui, tmp_path: Path, monkeypatch) -> None:
    app, widget, _, errors = ui
    source = _telemetry(tmp_path / "Falcon-2026-07-01.csv")
    widget.service.generate(widget.service.preview({str(source.path): "Falcon"}))
    document = widget.service.read_log("Falcon")
    identity = stable_flight_id("Falcon", document.rows[0][1], document.rows[0][2])
    uploads = []

    class FakeClient:
        def create_workbook(self, title):
            return Workbook("a" * 30, f"https://docs.google.com/spreadsheets/d/{'a' * 30}/edit", title)

        def sync_workbook(self, workbook, tables):
            uploads.extend(tables)
            return SheetsSyncReport(workbook, 1, 1, 1, remote_notes={"Falcon": {identity: "Edited in Sheets"}})

    monkeypatch.setattr(widget, "_google_client", lambda credentials: FakeClient())
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    widget.credentials_edit.setText(str(tmp_path / "credentials.json"))
    widget.create_workbook()
    _settle(app, widget)
    assert "docs.google.com" in widget.workbook_edit.text()
    widget.review_sheets_sync()
    _settle(app, widget)
    assert not uploads
    assert widget.sync_workbook_button.text() == "Sync 1 flight now"
    widget.review_sheets_sync()
    _settle(app, widget)
    assert uploads and uploads[0].aircraft == "Falcon"
    assert widget.service.read_log("Falcon").rows[0][-1] != "Edited in Sheets"
    assert widget.import_notes_button.isEnabled()
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    widget._import_sheets_notes()
    _settle(app, widget)
    assert widget.service.read_log("Falcon").rows[0][-1] == "Edited in Sheets"
    assert not widget.import_notes_button.isEnabled()
    assert not errors


def test_invalid_settings_do_not_break_launch_or_enable_writes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    store = AppStore(tmp_path / "state.sqlite3")
    output = tmp_path / "output"
    output.mkdir()
    settings = output / ".flight-log-settings.json"
    settings.write_text("not valid json", encoding="utf-8")
    store.set_setting("flight_logs.output_dir", str(output))
    widget = FlightLogWidget(store)
    source = _telemetry(tmp_path / "Falcon-2026-07-01.csv")
    widget.set_library(tmp_path, [source])
    assert widget._settings_error
    assert not widget.preview_button.isEnabled()
    assert not widget.save_policy_button.isEnabled()
    assert settings.read_text(encoding="utf-8") == "not valid json"
    assert widget.shutdown()
    widget.deleteLater()
    app.processEvents()
    store.close()


def test_review_text_prioritizes_results_and_retains_notes_at_risk() -> None:
    text = _review_text({"files": [{"id": "secret_id", "candidate_id": "opaque", "metrics": {"Maximum Current(A)": 29, "Maximum custom_sensor(A)": 42}}], "rows_added": 2, "updates": [{"aircraft": "Falcon", "plane": "Falcon", "removed": [{"notes": "Preserve me"}]}]})
    assert text.startswith("Rows added: 2")
    assert "Maximum Current(A): 29" in text
    assert "Maximum custom_sensor(A): 42" in text
    assert "Preserve me" in text
    assert "secret_id" not in text and "opaque" not in text


def test_unsaved_telemetry_draft_blocks_preview_scope_change_and_close(ui, tmp_path: Path, monkeypatch) -> None:
    app, widget, _, _ = ui
    source = _telemetry(tmp_path / "library" / "Falcon-2026-07-01.csv")
    widget.set_library(tmp_path / "library", [source])
    widget.auto_temperature_check.setChecked(False)
    widget.preview_selected()
    assert not widget._busy and widget._preview is None
    widget.policy_aircraft_combo.setCurrentText("Falcon")
    assert widget.policy_aircraft_combo.currentData() == ""
    assert not widget.auto_temperature_check.isChecked()
    assert not widget.prepare_close()
    widget.save_telemetry_settings()
    _settle(app, widget)
    widget.preview_selected()
    _settle(app, widget)
    assert widget._preview is not None
    assert "Maximum Temp2(°C)" not in widget._preview.candidates[0]["metrics"]
    widget.auto_temperature_check.setChecked(True)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    widget.preview_selected()
    _settle(app, widget)
    assert not widget.auto_temperature_check.isChecked()


def test_pasted_workbook_sync_retains_snapshot_and_link_change_invalidates_import(ui, tmp_path: Path, monkeypatch) -> None:
    app, widget, _, errors = ui
    source = _telemetry(tmp_path / "Falcon-2026-07-01.csv")
    widget.service.generate(widget.service.preview({str(source.path): "Falcon"}))
    document = widget.service.read_log("Falcon")
    identity = stable_flight_id("Falcon", document.rows[0][1], document.rows[0][2])

    class FakeClient:
        def sync_workbook(self, workbook, tables):
            return SheetsSyncReport(workbook, 1, 1, 1, remote_notes={"Falcon": {identity: "Sheets edit"}})

    monkeypatch.setattr(widget, "_google_client", lambda credentials: FakeClient())
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    widget.credentials_edit.setText(str(tmp_path / "credentials.json"))
    widget.workbook_edit.setText("b" * 30)
    widget.review_sheets_sync()
    _settle(app, widget)
    assert widget._sheets_local_documents["Falcon"].fingerprint == document.fingerprint
    widget.review_sheets_sync()
    _settle(app, widget)
    assert widget.import_notes_button.isEnabled()
    widget.workbook_edit.setText("c" * 30)
    assert not widget._sheets_local_documents
    assert not widget.import_notes_button.isEnabled()
    widget.workbook_edit.setText("https://example.com/not-a-workbook")
    widget._open_workbook()
    assert errors and "docs.google.com" in errors[-1]
