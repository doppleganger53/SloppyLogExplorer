from __future__ import annotations

import logging
import os
import sys
import tempfile
import traceback
from pathlib import Path
from typing import cast

from PyQt6.QtWidgets import QApplication

from . import __version__
from .main_window import MainWindow
from .models import GpsGradientOptions
from .parser import load_log
from .plotting import build_gps_map_html, build_telemetry_figure, figure_html


def _argument_value(flag: str) -> str | None:
    prefix = f"{flag}="
    for arg in sys.argv:
        if arg.startswith(prefix):
            return arg[len(prefix) :]

    try:
        index = sys.argv.index(flag)
    except ValueError:
        return None
    if index + 1 >= len(sys.argv):
        raise ValueError(f"{flag} requires a path argument")

    value_parts: list[str] = []
    for arg in sys.argv[index + 1 :]:
        if arg.startswith("--"):
            break
        value_parts.append(arg)
    if not value_parts:
        raise ValueError(f"{flag} requires a path argument")
    return " ".join(value_parts)


def _validate_log(path: str) -> None:
    log = load_log(Path(path))
    columns = log.parameter_columns[:4]
    if not columns:
        raise ValueError(f"No numeric telemetry columns found in {path}")
    fig = build_telemetry_figure(log, columns, selected_index=0)
    rendered = figure_html(fig, bridge=True)
    trace_count = len(cast(tuple[object, ...], fig.data))
    if trace_count == 0:
        raise ValueError(f"No graph traces rendered for {path}")
    if "QWebChannel" not in rendered:
        raise ValueError("Rendered telemetry HTML is missing the Qt bridge")
    if log.info.has_gps:
        gps_html = build_gps_map_html(log, GpsGradientOptions(color_column=columns[0]))
        if "maplibregl.Map" not in gps_html or "tile.openstreetmap.org" not in gps_html:
            raise ValueError("Rendered GPS map HTML is missing MapLibre or OpenStreetMap raster tiles")
    message = (
        f"validated {log.info.name}: rows={log.info.rows} "
        f"columns={log.info.columns} traces={trace_count} duration={log.info.duration_seconds:.2f}s"
    )
    print(message)
    _write_validation_log(message)


def _validate_ui_log(app: QApplication, path: str) -> None:
    window = MainWindow()
    window.load_log(Path(path))
    if window.current_log is None:
        raise ValueError(f"UI did not load {path}")
    columns = window.selected_columns()
    if not columns:
        raise ValueError(f"UI did not select telemetry columns for {path}")
    if window.current_log.info.rows <= 0:
        raise ValueError(f"UI loaded an empty log for {path}")
    message = (
        f"validated UI {window.current_log.info.name}: rows={window.current_log.info.rows} "
        f"columns={window.current_log.info.columns} selected={len(columns)}"
    )
    print(message)
    _write_validation_log(message)
    window.close()


def _write_validation_log(message: str) -> None:
    log_path = Path(tempfile.gettempdir()) / "SloppyLogExplorer-validate.log"
    log_path.write_text(message + "\n", encoding="utf-8")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if "--version" in sys.argv:
        print(__version__)
        return
    validate_path = _argument_value("--validate-log")
    if validate_path:
        try:
            _validate_log(validate_path)
        except Exception:
            _write_validation_log(traceback.format_exc())
            raise SystemExit(1)
        return
    validate_ui_path = _argument_value("--validate-ui-log")
    if validate_ui_path:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    smoke_test = "--smoke-test" in sys.argv
    if smoke_test:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if smoke_test or validate_ui_path:
        sys.argv = [
            arg
            for arg in sys.argv
            if arg not in {"--smoke-test", "--validate-ui-log", validate_ui_path}
            and not arg.startswith("--validate-ui-log=")
        ]

    app = QApplication(sys.argv)
    app.setApplicationName("Sloppy Log Explorer")
    app.setOrganizationName("SloppyLogExplorer")
    if validate_ui_path:
        try:
            _validate_ui_log(app, validate_ui_path)
        except Exception:
            _write_validation_log(traceback.format_exc())
            raise SystemExit(1)
        return
    window = MainWindow()
    if smoke_test:
        print(window.windowTitle())
        window.close()
        return
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
