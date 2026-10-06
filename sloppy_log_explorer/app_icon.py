"""Shared app icon path for source installs and PyInstaller bundles."""

from pathlib import Path

from PyQt6.QtGui import QIcon

APP_ICON_PATH = Path(__file__).resolve().parent / "assets" / "app-icon.ico"


def application_icon() -> QIcon:
    return QIcon(str(APP_ICON_PATH))
