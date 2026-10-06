from PyQt6.QtCore import QSize
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

from sloppy_log_explorer.app_icon import APP_ICON_PATH, application_icon
from tools.make_app_icon import ICON_SIZES


def test_app_icon_renders_every_windows_size_with_transparent_corners() -> None:
    app = QApplication.instance() or QApplication([])
    icon = application_icon()
    assert not icon.isNull()
    assert sorted(size.width() for size in icon.availableSizes()) == list(ICON_SIZES)
    for size in ICON_SIZES:
        image = icon.pixmap(QSize(size, size), 1.0).toImage()
        assert not image.isNull()
        assert (image.width(), image.height()) == (size, size)
        assert image.hasAlphaChannel()
        assert image.pixelColor(0, 0).alpha() == 0
    master = QImage(str(APP_ICON_PATH.with_suffix(".png")))
    assert not master.isNull()
    assert master.width() == master.height()
    assert app is not None
