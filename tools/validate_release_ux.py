"""Capture release UI states with isolated synthetic data and app settings.

This runs the real Qt widgets and WebEngine, then writes only synthetic-flight
screenshots and a machine-readable interaction report to the output directory.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "validation_artifacts" / "release-readiness" / "ux")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    print("Starting isolated release UX validation", flush=True)
    with tempfile.TemporaryDirectory(prefix="sloppy-release-ux-") as temp_dir:
        root = Path(temp_dir)
        os.environ["APPDATA"] = str(root / "appdata")
        os.environ.pop("QT_QPA_PLATFORM", None)
        from PyQt6.QtCore import QEventLoop, QTimer
        from PyQt6 import sip
        from PyQt6.QtWidgets import QApplication, QMessageBox, QTableWidgetItem
        from sloppy_log_explorer.main_window import MainWindow
        from sloppy_log_explorer.parser import load_log
        from sloppy_log_explorer import qt_plot

        app = QApplication.instance() or QApplication([sys.argv[0]])
        errors: list[str] = []
        QMessageBox.warning = lambda _parent, title, message: errors.append(f"{title}: {message}")  # type: ignore[method-assign]

        def wait(milliseconds: int = 150) -> None:
            loop = QEventLoop()
            QTimer.singleShot(milliseconds, loop.quit)
            loop.exec()

        def until(predicate, timeout: float = 45.0) -> None:
            deadline = time.monotonic() + timeout
            while not predicate():
                if time.monotonic() >= deadline:
                    raise AssertionError("The UI operation did not finish before its timeout.")
                wait(100)

        library = root / "library"
        library.mkdir()
        headers = "Date,Time,VFAS(V),Current(A),GPS Lat,GPS Lon,Alt(m),VFR(%),VFR(%),Tx Power(mW)"
        for flight in range(2):
            rows = [headers]
            for index in range(601):
                angle = index * math.pi / 150
                seconds = index // 2
                timestamp = f"12:{seconds // 60:02d}:{seconds % 60:02d}.{(index % 2) * 5}"
                values = [
                    f"2026-01-0{flight + 1}", timestamp,
                    f"{16.8 - index * 0.001:.3f}", f"{10 + 8 * math.sin(angle):.3f}",
                    f"{39.5 + 0.0008 * math.cos(angle):.7f}",
                    f"{-75.5 + 0.001 * math.sin(angle):.7f}",
                    f"{30 + 10 * math.sin(angle):.3f}", f"{95 + 4 * math.sin(angle):.3f}",
                    f"{94 + 5 * math.sin(angle):.3f}", "100",
                ]
                rows.append(",".join(values))
            (library / f"Demo-2026-01-0{flight + 1}.csv").write_text("\n".join(rows), encoding="utf-8")

        window = MainWindow()
        print("Main window initialized", flush=True)
        window.show()
        wait(300)
        captures = []
        telemetry_layouts = []

        def inspect_telemetry() -> None:
            loop = QEventLoop()
            result = []
            assert window.graph_view._page is not None
            window.graph_view._page.runJavaScript(
                """(() => {
                const plot = document.querySelector('.plotly-graph-div');
                const legend = document.querySelector('.legend')?.getBoundingClientRect();
                const stats = document.querySelector('.annotation')?.getBoundingClientRect();
                return {background: plot?._fullLayout?.paper_bgcolor, cloudUpload: plot?._context?.showSendToCloud,
                  cloudButtons: Array.from(document.querySelectorAll('.modebar-btn')).map(button => button.getAttribute('data-title') || '').filter(title => /share chart|cloud/i.test(title)),
                  overlaps: !!(legend && stats && legend.left < stats.right && legend.right > stats.left && legend.top < stats.bottom && legend.bottom > stats.top)};
                })()""",
                lambda value: (result.append(value), loop.quit()),
            )
            QTimer.singleShot(3000, loop.quit)
            loop.exec()
            if not result or not result[0]:
                raise AssertionError("Telemetry page could not be inspected.")
            expected = "#1f242b" if window.dark_mode else "#ffffff"
            if result[0]["background"] != expected or result[0]["overlaps"] or result[0]["cloudUpload"] is not False or result[0]["cloudButtons"]:
                raise AssertionError(f"Telemetry theme or legend layout is incorrect: {result[0]}")
            telemetry_layouts.append(result[0])

        def capture(name: str) -> None:
            wait(200)
            filename = f"{name}.png"
            if not window.grab().save(str(args.output / filename)):
                raise AssertionError(f"Could not save {filename}")
            captures.append({"file": filename, "width": window.width(), "height": window.height()})
            print(f"Captured {filename}", flush=True)

        capture("empty-dark")
        window.load_library(library)
        until(lambda: window._reception_scan_task is None)
        window.load_log(library / "Demo-2026-01-01.csv")
        assert window.current_log is not None
        window.compare_log = load_log(library / "Demo-2026-01-02.csv")
        window.compare_label.setText("Demo-2026-01-02.csv")
        window.compare_toggle.setChecked(True)
        window.telemetry_time_combo.setCurrentText("Relative")
        window.set_selected_index(200)
        window.tabs.setCurrentWidget(window.flight_notes_tab)
        window.flight_notes.setPlainText("Synthetic demo flight. Smooth circuit; compare power and reception across the field.")
        window.save_flight_notes()
        window.battery_name.setText("Demo 4S 2200 mAh")
        window.add_battery()
        window.store.add_battery_history(1, str(window.current_log.info.path), 25.0, 6.25, "Good")
        window.refresh_batteries()
        window.sync_source.setText(str(library))
        window.sync_target.setText(str(root / "synced"))
        window.scan_sync()
        window.voice_table.setRowCount(2)
        for row, (text, filename) in enumerate([("Landing gear down", "gear_down.wav"), ("Battery low", "battery_low.wav")]):
            window.voice_table.setItem(row, 0, QTableWidgetItem(text))
            window.voice_table.setItem(row, 1, QTableWidgetItem(filename))
        window.tabs.setCurrentWidget(window.gps_tab)
        window.gps_color_combo.setCurrentText("Current(A)")
        window.gps_marker_value_combos[0].setCurrentText("VFAS(V)")
        window.tabs.setCurrentWidget(window.reception_tab)
        until(lambda: window.reception_generate_button.isEnabled())
        window.generate_reception_heatmap()
        until(lambda: window._reception_heatmap_task is None)
        until(lambda: window.reception_view.ready)
        assert window._reception_map_payload

        for width, height in [(1500, 940), (1280, 800)]:
            for dark in [True, False]:
                window.dark_action.setChecked(dark)
                window.toggle_theme()
                theme = "dark" if dark else "light"
                for index in range(window.tabs.count()):
                    window.tabs.setCurrentIndex(index)
                    window.resize(width, height)
                    if index == 0:
                        until(lambda: window.graph_view._loaded_render_generation == window.graph_view.render_generation)
                        wait(300)
                        inspect_telemetry()
                    if window.tabs.currentWidget() is window.reception_tab:
                        if window._reception_map_payload is None:
                            window.generate_reception_heatmap()
                            until(lambda: window._reception_heatmap_task is None)
                        until(lambda: window.reception_view.ready)
                    wait(350 if window.tabs.currentWidget() in (window.gps_tab, window.reception_tab) else 100)
                    tab_name = window.tabs.tabText(index).lower().replace(" ", "-")
                    if window.width() != width or window.height() != height:
                        raise AssertionError(f"{tab_name} cannot fit {width}x{height}: {window.width()}x{window.height()}")
                    capture(f"{width}x{height}-{theme}-{tab_name}")
                window.tabs.setCurrentWidget(window.reception_tab)
                window.reception_normalize_check.setChecked(True)
                window.resize(width, height)
                wait()
                if window.width() != width or window.height() != height:
                    raise AssertionError("Expanded normalization controls exceed the requested window size.")
                capture(f"{width}x{height}-{theme}-normalization")
                window.reception_normalize_check.setChecked(False)

        report = {
            "settings_isolated": True,
            "synthetic_flights": 2,
            "rows_per_flight": 601,
            "tabs_evaluated": [window.tabs.tabText(index) for index in range(window.tabs.count())],
            "interactions": ["empty state", "library scan", "log load", "compare", "relative time", "cursor seek", "notes save", "battery registration/history", "sync scan", "GPS coloring", "heatmap generation", "normalization controls", "themes", "window resizing"],
            "warnings": errors,
            "telemetry_layout_checks": telemetry_layouts,
            "captures": captures,
        }
        (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        window.close()
        sip.delete(window)
        if qt_plot._WEB_PROFILE is not None:
            sip.delete(qt_plot._WEB_PROFILE)
            qt_plot._WEB_PROFILE = None
        wait(100)
        app.quit()
        print(json.dumps({"captures": len(captures), "warnings": errors, "report": str(args.output / "report.json")}))
        if errors:
            raise AssertionError("UI warnings occurred; inspect report.json.")


if __name__ == "__main__":
    main()
