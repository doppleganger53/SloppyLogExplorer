from __future__ import annotations

import logging
import os
import sys

from PyQt6.QtWidgets import QApplication

from . import __version__
from .main_window import MainWindow


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if "--version" in sys.argv:
        print(__version__)
        return

    smoke_test = "--smoke-test" in sys.argv
    if smoke_test:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        sys.argv = [arg for arg in sys.argv if arg != "--smoke-test"]

    app = QApplication(sys.argv)
    app.setApplicationName("Sloppy Log Explorer")
    app.setOrganizationName("SloppyLogExplorer")
    window = MainWindow()
    if smoke_test:
        print(window.windowTitle())
        window.close()
        return
    window.show()
    raise SystemExit(app.exec())
