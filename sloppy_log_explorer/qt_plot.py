from __future__ import annotations

import os

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QTextEdit, QVBoxLayout, QWidget

from .models import LoadedLog
from .plotting import build_gps_figure, build_telemetry_figure, figure_html


class _PlotBridge(QObject):
    index_selected = pyqtSignal(int)
    index_stepped = pyqtSignal(int)

    @pyqtSlot(int)
    def selectIndex(self, index: int) -> None:
        self.index_selected.emit(index)

    @pyqtSlot(int)
    def stepIndex(self, delta: int) -> None:
        self.index_stepped.emit(delta)


def _use_web_engine() -> bool:
    return os.environ.get("QT_QPA_PLATFORM", "").lower() != "offscreen"


class TelemetryPlotWidget(QWidget):
    index_selected = pyqtSignal(int)
    index_stepped = pyqtSignal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.log: LoadedLog | None = None
        self.compare: LoadedLog | None = None
        self.columns: list[str] = []
        self.selected_index = 0
        self.show_grid = True
        self.dark = True
        self.interaction_mode = "pan"
        self.time_mode = "absolute"
        self.setMinimumHeight(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._web_engine = _use_web_engine()
        if self._web_engine:
            self._view = QWebEngineView(self)
            self._bridge = _PlotBridge()
            self._bridge.index_selected.connect(self.index_selected)
            self._bridge.index_stepped.connect(self.index_stepped)
            self._channel = QWebChannel(self._view.page())
            self._channel.registerObject("plotBridge", self._bridge)
            self._view.page().setWebChannel(self._channel)
        else:
            self._view = QTextEdit(self)
            self._view.setReadOnly(True)
        layout.addWidget(self._view)

    def set_plot(
        self,
        log: LoadedLog | None,
        columns: list[str],
        compare: LoadedLog | None = None,
        selected_index: int = 0,
        show_grid: bool = True,
        dark: bool = True,
        interaction_mode: str | None = None,
        time_mode: str | None = None,
    ) -> None:
        self.log = log
        self.compare = compare
        self.columns = columns
        self.selected_index = selected_index
        self.show_grid = show_grid
        self.dark = dark
        if interaction_mode:
            self.interaction_mode = interaction_mode
        if time_mode:
            self.time_mode = time_mode
        self._render()

    def set_interaction_mode(self, mode: str) -> None:
        self.interaction_mode = mode
        self._render()

    def set_time_mode(self, mode: str) -> None:
        self.time_mode = mode
        self._render()

    def reset_view(self) -> None:
        self._render()

    def _render(self) -> None:
        fig = build_telemetry_figure(
            self.log,
            self.columns,
            compare=self.compare,
            selected_index=self.selected_index,
            show_grid=self.show_grid,
            dark=self.dark,
            interaction_mode=self.interaction_mode,
            time_mode=self.time_mode,
        )
        html = figure_html(fig, bridge=self._web_engine, dark=self.dark)
        self._view.setHtml(html)


class GpsPathWidget(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.log: LoadedLog | None = None
        self.color_column: str | None = None
        self.dark = True
        self.setMinimumHeight(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._web_engine = _use_web_engine()
        if self._web_engine:
            self._view = QWebEngineView(self)
        else:
            self._view = QTextEdit(self)
            self._view.setReadOnly(True)
        layout.addWidget(self._view)

    def set_path(self, log: LoadedLog | None, color_column: str | None = None, dark: bool = True) -> None:
        self.log = log
        self.color_column = color_column
        self.dark = dark
        fig = build_gps_figure(log, color_column=color_column, dark=dark)
        self._view.setHtml(figure_html(fig, dark=dark))
