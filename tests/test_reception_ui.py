from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QDate
from PyQt6.QtWidgets import QApplication


def _record(path: Path, flight_date: str, *, inferred: bool = False, channels: list[str] | None = None) -> dict[str, object]:
    return {
        "file_path": path,
        "file_size": 100,
        "mtime_ns": 1,
        "status": "ok",
        "flight_date": flight_date,
        "date_inferred": inferred,
        "center_latitude": 39.75,
        "center_longitude": -75.25,
        "channels": channels or ["VFR 2.4G(%)"],
        "error": "",
    }


def test_reception_tab_uses_cached_site_dates_and_exact_channel_coverage(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from sloppy_log_explorer.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    root = tmp_path / "library"
    root.mkdir()
    records = [
        _record(root / "first.csv", "2026-04-01", channels=["VFR 2.4G(%)", "RSSI 2.4G(dB)"]),
        _record(root / "second.csv", "2026-04-03", inferred=True),
    ]
    window.store.upsert_reception_records(root, records)
    sites = window.store.apply_reception_clusters(
        root,
        [{"file_paths": [record["file_path"] for record in records], "center_latitude": 39.75, "center_longitude": -75.25}],
    )
    window.store.update_flying_site(int(sites[0]["id"]), "WJRC", "West field")
    window.library_root = root

    window._load_cached_reception_sites(preserve_selection=False)

    assert window.reception_site_combo.count() == 1
    assert window.reception_site_combo.currentText() == "WJRC (2 logs)"
    assert window.reception_site_name.text() == "WJRC"
    assert window.reception_site_notes.toPlainText() == "West field"
    assert window.reception_date_from.date() == QDate(2026, 4, 1)
    assert window.reception_date_to.date() == QDate(2026, 4, 3)
    choices = [window.reception_channel_combo.itemText(index) for index in range(window.reception_channel_combo.count())]
    assert "VFR 2.4G(%) — 2/2 logs" in choices
    assert "RSSI 2.4G(dB) — 1/2 logs" in choices

    window.reception_date_from.setDate(QDate(2026, 4, 3))
    filtered = [window.reception_channel_combo.itemText(index) for index in range(window.reception_channel_combo.count())]
    assert filtered == ["VFR 2.4G(%) — 1/1 logs"]

    window.reception_site_name.setText("WJRC Main Field")
    window.reception_site_notes.setPlainText("Club-owned flying site")
    window.save_reception_site_metadata()
    saved = window.store.list_flying_sites(root)[0]
    assert saved["name"] == "WJRC Main Field"
    assert saved["notes"] == "Club-owned flying site"
    window.close()
    app.quit()


def test_opening_library_starts_reception_scan_without_changing_metadata_scan(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from sloppy_log_explorer.main_window import MainWindow

    starts: list[Path | None] = []
    monkeypatch.setattr(MainWindow, "start_reception_scan", lambda self, *_args: starts.append(self.library_root))
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    root = tmp_path / "library"
    root.mkdir()
    (root / "flight.csv").write_text("not parsed by scan_library", encoding="utf-8")

    window.load_library(root)

    assert len(window.library_logs) == 1
    assert starts == [root]
    assert window.reception_scan_refresh_button.isEnabled()
    assert window._reception_view_loaded is False
    window.tabs.setCurrentWidget(window.reception_tab)
    assert window._reception_view_loaded is True
    window.close()
    app.quit()


def test_reception_site_selector_sorts_display_names_case_insensitively(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from sloppy_log_explorer.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    root = tmp_path / "library"
    root.mkdir()
    records = [
        _record(root / "one.csv", "2026-04-01"),
        _record(root / "two.csv", "2026-04-02"),
        _record(root / "three.csv", "2026-04-03"),
    ]
    window.store.upsert_reception_records(root, records)
    sites = window.store.apply_reception_clusters(
        root,
        [
            {"file_paths": [records[0]["file_path"]], "center_latitude": 39.70, "center_longitude": -75.20},
            {"file_paths": [records[1]["file_path"]], "center_latitude": 39.80, "center_longitude": -75.30},
            {"file_paths": [records[2]["file_path"]], "center_latitude": 39.90, "center_longitude": -75.40},
        ],
    )
    window.store.update_flying_site(int(sites[0]["id"]), "Zulu", "")
    window.store.update_flying_site(int(sites[1]["id"]), "", "")
    window.store.update_flying_site(int(sites[2]["id"]), "alpha", "")
    window.library_root = root

    window._load_cached_reception_sites(preserve_selection=False)

    labels = [window.reception_site_combo.itemText(index) for index in range(3)]
    assert labels[0] == "alpha (1 logs)"
    assert labels[1].startswith("Flying Site ")
    assert labels[2] == "Zulu (1 logs)"
    window.close()
    app.quit()


def test_reception_filter_change_rejects_a_queued_stale_heatmap(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from sloppy_log_explorer.main_window import MainWindow
    from sloppy_log_explorer.reception_workers import ReceptionTask

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    root = tmp_path / "library"
    root.mkdir()
    record = _record(
        root / "flight.csv",
        "2026-04-01",
        channels=["VFR 2.4G(%)", "RSSI 2.4G(dB)"],
    )
    window.store.upsert_reception_records(root, [record])
    window.store.apply_reception_clusters(
        root,
        [{"file_paths": [record["file_path"]], "center_latitude": 39.75, "center_longitude": -75.25}],
    )
    window.library_root = root
    window._load_cached_reception_sites(preserve_selection=False)

    queued_generation = window._reception_heatmap_generation
    queued_token = window._reception_selection_token()
    queued_task = ReceptionTask(lambda _progress, _is_cancelled: None)
    window._reception_heatmap_task = queued_task
    payload = {
        "status": "ok",
        "cells": [],
        "value_min": 0.0,
        "value_max": 100.0,
        "logs_used": 1,
        "logs_considered": 1,
        "missing_channel_count": 0,
        "error_count": 0,
        "date_inferred_count": 0,
    }

    next_index = 1 if window.reception_channel_combo.currentIndex() == 0 else 0
    window.reception_channel_combo.setCurrentIndex(next_index)

    assert window._reception_heatmap_generation > queued_generation
    assert queued_task.is_cancelled()
    assert window._reception_map_payload is None
    assert "Telemetry item changed" in window.reception_result_status.text()
    window._reception_heatmap_completed(queued_generation, queued_token, payload)
    assert window._reception_map_payload is None
    window._reception_heatmap_task = None

    window.close()
    app.quit()
