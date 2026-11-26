from datetime import date
from time import monotonic
from zipfile import ZipFile

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication


def test_history_refreshes_on_library_change_and_rejects_old_site_results(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "settings"))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from sloppy_log_explorer.main_window import MainWindow

    library = tmp_path / "library"
    library.mkdir()

    def recording(day: str, latitude: float) -> str:
        return (
            "Date,Time,GPS Lat,GPS Lon,VFR 900M(%)\n"
            + "".join(f"{day},12:00:{i:02},{latitude},-75.0,80\n" for i in range(25))
        )

    first = library / "Glider-2026-01-01.csv"
    first.write_text(recording("2026-01-01", 40), encoding="utf-8")
    with ZipFile(library / "recordings.zip", "w") as archive:
        archive.write(first, "backup/renamed.csv")
        archive.writestr("Trainer/remote.log", recording("2026-06-01", 41))

    app = QApplication.instance() or QApplication([])
    window = MainWindow()

    def wait_for_workers():
        deadline = monotonic() + 20
        while window._reception_tasks:
            assert monotonic() < deadline, window.reception_result_status.text()
            QTest.qWait(10)

    try:
        window.load_library(library)
        wait_for_workers()
        assert window.reception_site_combo.count() == 2
        assert "1 duplicate" in window.reception_scan_status.text()
        sites = sorted(window.reception_sites, key=lambda site: site["center_latitude"])
        select = window.reception_site_combo
        select.setCurrentIndex(select.findData(sites[0]["id"]))
        window.tabs.setCurrentWidget(window.reception_tab)
        with monkeypatch.context() as temporary_failure:
            temporary_failure.setattr(
                "sloppy_log_explorer.history_sources.build_site_history",
                lambda *args, **kwargs: {"logs": [], "counts": {"read_error": 1}},
            )
            window.reception_mode_combo.setCurrentIndex(window.reception_mode_combo.findData("history"))
            wait_for_workers()
            assert "read error" in window.reception_result_status.text()
        window.refresh_reception_index()
        wait_for_workers()
        assert window.reception_static_controls.isHidden()
        assert window.reception_basemap_combo.currentData() == "offline"
        first_payload = window._reception_map_payload
        assert [log["date"] for log in first_payload["logs"]] == ["2026-01-01"]

        generation = window._reception_heatmap_generation
        token = window._reception_selection_token()
        window.refresh_reception_index()
        wait_for_workers()
        assert window._reception_heatmap_generation == generation
        assert window._reception_map_payload is first_payload

        (library / "Glider-2026-05-01.csv").write_text(recording("2026-05-01", 40.0002))
        window.refresh_reception_index()
        wait_for_workers()
        assert [log["date"] for log in window._reception_map_payload["logs"]] == ["2026-01-01", "2026-05-01"]
        assert window._reception_map_payload["center"] == first_payload["center"]

        select.setCurrentIndex(select.findData(sites[1]["id"]))
        wait_for_workers()
        window._reception_heatmap_completed(generation, token, first_payload)
        assert [log["date"] for log in window._reception_map_payload["logs"]] == ["2026-06-01"]
        window.reception_mode_combo.setCurrentIndex(window.reception_mode_combo.findData("combined"))
        assert window.reception_basemap_combo.findData("offline") < 0
        assert window.reception_date_from.date().toPyDate() == date(2026, 6, 1)
    finally:
        window.close()
