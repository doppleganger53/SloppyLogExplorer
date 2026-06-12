"""Qt widgets that render Plotly graphs and the GPS map."""

from __future__ import annotations

import os
import json
import tempfile
from pathlib import Path
from typing import cast

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QTextEdit, QVBoxLayout, QWidget

from .models import GpsGradientOptions, LoadedLog
from .plotting import build_gps_map_html, build_telemetry_figure, figure_html
from .storage import app_data_dir


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
    # Offscreen validation and CLI runs use plain text instead of Qt WebEngine
    # because the embedded browser is unnecessary and can fail headlessly.
    return os.environ.get("QT_QPA_PLATFORM", "").lower() != "offscreen"


_WEB_PROFILE: QWebEngineProfile | None = None


def _persistent_web_profile() -> QWebEngineProfile:
    global _WEB_PROFILE
    if _WEB_PROFILE is None:
        # Persist cache/storage under the app data directory so map tiles and
        # WebEngine state survive across launches instead of starting cold.
        root = app_data_dir() / "webengine"
        cache = root / "cache"
        storage = root / "storage"
        cache.mkdir(parents=True, exist_ok=True)
        storage.mkdir(parents=True, exist_ok=True)
        profile = QWebEngineProfile("SloppyLogExplorer", None)
        profile.setCachePath(str(cache))
        profile.setPersistentStoragePath(str(storage))
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        profile.setHttpUserAgent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) SloppyLogExplorer/0.1 QtWebEngine"
        )
        _WEB_PROFILE = profile
    return _WEB_PROFILE


def _write_temp_html(html: str, prefix: str) -> Path:
    # WebEngine loads the GPS map from a local file URL, so write a temporary
    # HTML file into an app-owned directory rather than an anonymous temp file.
    html_dir = app_data_dir() / "rendered_html"
    html_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        suffix=".html",
        prefix=prefix,
        dir=html_dir,
        delete=False,
    ) as handle:
        handle.write(html)
        return Path(handle.name)


def _remove_file(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


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
        self._view: QWebEngineView | QTextEdit
        self.setMinimumHeight(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._web_engine = _use_web_engine()
        if self._web_engine:
            self._view = QWebEngineView(self)
            page = self._view.page()
            if page is None:
                raise RuntimeError("QWebEngineView.page() returned None")
            self._bridge = _PlotBridge()
            self._bridge.index_selected.connect(self.index_selected)
            self._bridge.index_stepped.connect(self.index_stepped)
            self._channel = QWebChannel(page)
            self._channel.registerObject("plotBridge", self._bridge)
            page.setWebChannel(self._channel)
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
        # The Plotly figure is rebuilt on every state change so compare traces,
        # cursor selection, and display mode stay in sync with the main window.
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
        self.options = GpsGradientOptions()
        self.dark = True
        self._view: QWebEngineView | QTextEdit
        self._html_path: Path | None = None
        self._page: QWebEnginePage | None = None
        self._pending_viewport_fit = False
        self._viewport_refresh_timer = QTimer(self)
        self._viewport_refresh_timer.setSingleShot(True)
        self._viewport_refresh_timer.timeout.connect(self._run_scheduled_viewport_refresh)
        self.setMinimumHeight(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._web_engine = _use_web_engine()
        if self._web_engine:
            self._view = QWebEngineView(self)
            self._page = QWebEnginePage(_persistent_web_profile(), self._view)
            settings = self._page.settings()
            if settings is None:
                raise RuntimeError("QWebEnginePage.settings() returned None")
            settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
            self._page.loadFinished.connect(self._map_page_loaded)
            self._view.setPage(self._page)
        else:
            self._view = QTextEdit(self)
            self._view.setReadOnly(True)
        layout.addWidget(self._view)

    def set_path(
        self,
        log: LoadedLog | None,
        options: GpsGradientOptions | None = None,
        dark: bool = True,
    ) -> None:
        self.log = log
        self.options = options or GpsGradientOptions()
        self.dark = dark
        html = build_gps_map_html(log, options=self.options, dark=dark)
        if self._web_engine:
            # Regenerate the HTML file on each refresh so the embedded browser
            # always reads the latest payload and can access local assets.
            previous_path = self._html_path
            self._html_path = _write_temp_html(html, "gps-map-")
            cast(QWebEngineView, self._view).setUrl(QUrl.fromLocalFile(str(self._html_path)))
            _remove_file(previous_path)
            self._schedule_viewport_refresh(fit=True)
        else:
            self._view.setHtml(html)

    def refresh_viewport(self, fit: bool = False) -> None:
        if not self._web_engine:
            return
        page = self._page
        if page is None:
            return
        options = json.dumps({"fit": fit})
        page.runJavaScript(f"window.sloppyGpsMap ? window.sloppyGpsMap.refresh({options}) : false;")

    def _schedule_viewport_refresh(self, fit: bool = False) -> None:
        try:
            web_engine = self._web_engine
            timer = self._viewport_refresh_timer
        except (AttributeError, RuntimeError):
            return
        if not web_engine:
            return
        self._pending_viewport_fit = self._pending_viewport_fit or fit
        if not timer.isActive():
            timer.start(90)

    def _run_scheduled_viewport_refresh(self) -> None:
        fit = self._pending_viewport_fit
        self._pending_viewport_fit = False
        self.refresh_viewport(fit=fit)

    def _map_page_loaded(self, ok: bool) -> None:
        if ok:
            self._schedule_viewport_refresh(fit=True)

    def showEvent(self, a0) -> None:
        super().showEvent(a0)
        self._schedule_viewport_refresh(fit=True)

    def resizeEvent(self, a0) -> None:
        super().resizeEvent(a0)
        self._schedule_viewport_refresh(fit=False)

    def closeEvent(self, a0) -> None:
        _remove_file(self._html_path)
        self._html_path = None
        super().closeEvent(a0)
