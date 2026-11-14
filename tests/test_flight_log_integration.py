from __future__ import annotations

from pathlib import Path
from threading import Event
import time

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import QApplication, QMessageBox


def _wait(view) -> None:
    deadline = time.monotonic() + 5
    while view._busy or view._tasks:
        assert time.monotonic() < deadline, "Flight Logs worker did not finish"
        QApplication.processEvents()
        time.sleep(0.01)


def test_library_and_tools_menu_connect_flight_logs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from sloppy_log_explorer.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        library = tmp_path / "library"
        archive = library / "archive"
        archive.mkdir(parents=True)
        (archive / "Falcon-2026-10-01.csv").write_text(
            "Date,Time,Current(A)\n2026-10-01,12:00:00,1\n2026-10-01,12:00:01,2\n",
            encoding="utf-8",
        )
        window.load_library(library)
        view = window.flight_logs_tab
        assert window.tabs.tabText(window.tabs.indexOf(view)) == "Flight Logs"
        assert view.selected_aircraft() == ["Falcon"]
        menu_bar = window.menuBar()
        assert menu_bar is not None
        tools = next(action.menu() for action in menu_bar.actions() if action.text() == "Tools")
        assert tools is not None
        action = next(action for action in tools.actions() if action.text() == "Manage Flight Logs")
        action.trigger()
        assert window.tabs.currentWidget() is view
        aircraft_item = view.aircraft_list.item(0)
        assert aircraft_item is not None
        aircraft_item.setCheckState(Qt.CheckState.Unchecked)
        window.refresh_reception_index()
        assert view.selected_aircraft() == []
        assert not view.preview_button.isEnabled()
        _wait(view)
    finally:
        window.close()
        app.processEvents()


def test_main_window_close_keeps_store_alive_until_flight_log_worker_finishes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    notices: list[str] = []
    monkeypatch.setattr(QMessageBox, "information", lambda _parent, _title, text: notices.append(text))
    from sloppy_log_explorer.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    released = Event()
    try:
        view = window.flight_logs_tab
        view._run("Synthetic background operation", lambda: released.wait(5), lambda _result: None)
        event = QCloseEvent()
        window.closeEvent(event)
        assert not event.isAccepted()
        assert notices
        window.store.set_setting("worker-close-check", True)
        assert window.store.get_setting("worker-close-check") is True
        released.set()
        _wait(view)
    finally:
        released.set()
        _wait(window.flight_logs_tab)
        window.close()
        app.processEvents()


def test_retaining_telemetry_draft_cancels_library_change_before_app_state_mutates(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.No)
    from sloppy_log_explorer.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    view = window.flight_logs_tab
    try:
        original = tmp_path / "original"
        replacement = tmp_path / "replacement"
        original.mkdir()
        replacement.mkdir()
        (original / "Falcon-2026-10-01.csv").write_text("Date,Time\n2026-10-01,12:00:00\n", encoding="utf-8")
        (replacement / "Trainer-2026-10-01.csv").write_text("Date,Time\n2026-10-01,12:00:00\n", encoding="utf-8")
        window.load_library(original)
        assert view.auto_vfr_check.isChecked()
        view.auto_vfr_check.setChecked(False)
        window.load_library(replacement)
        assert window.library_root == original
        assert window.store.get_setting("library_root") == str(original)
        assert view.selected_aircraft() == ["Falcon"]
        assert not view.auto_vfr_check.isChecked()
    finally:
        view.auto_vfr_check.setChecked(True)
        _wait(view)
        window.close()
        app.processEvents()
