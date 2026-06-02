from __future__ import annotations

import logging
import sys

from PyQt6.QtWidgets import QApplication

from .main_window import MainWindow


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = QApplication(sys.argv)
    app.setApplicationName("Sloppy Log Explorer")
    app.setOrganizationName("SloppyLogExplorer")
    window = MainWindow()
    window.show()
    raise SystemExit(app.exec())

