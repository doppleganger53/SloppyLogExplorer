"""Qt widgets that render Plotly graphs and the GPS map."""

from __future__ import annotations

from collections.abc import Sequence
import os
import json
import tempfile
from pathlib import Path
from typing import Any, cast

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QTextEdit, QVBoxLayout, QWidget

from .models import GpsGradientOptions, LoadedLog
from .plotting import (
    MAX_RENDERED_TELEMETRY_TRACES,
    MAX_TELEMETRY_TRACE_POINTS_HARD_CAP,
    TELEMETRY_MARGIN_LEFT,
    _telemetry_right_margin,
    _telemetry_axis_group_count,
    _telemetry_trace_point_budget,
    _telemetry_x_values,
    build_gps_map_html,
    build_telemetry_figure,
    figure_html,
)
from .storage import app_data_dir

TELEMETRY_RESIZE_RERENDER_MS = 150


class _PlotBridge(QObject):
    index_selected = pyqtSignal(int)
    index_stepped = pyqtSignal(int)
    x_range_changed = pyqtSignal(str)

    @pyqtSlot(int)
    def selectIndex(self, index: int) -> None:
        self.index_selected.emit(index)

    @pyqtSlot(int)
    def stepIndex(self, delta: int) -> None:
        self.index_stepped.emit(delta)

    @pyqtSlot(str)
    def setXRange(self, payload: str) -> None:
        self.x_range_changed.emit(payload)


class _GpsBridge(QObject):
    elapsed_seeked = pyqtSignal(float)
    playing_changed = pyqtSignal(bool)
    speed_changed = pyqtSignal(float)

    @pyqtSlot(float)
    def seekElapsed(self, elapsed_seconds: float) -> None:
        self.elapsed_seeked.emit(elapsed_seconds)

    @pyqtSlot(bool)
    def setPlaying(self, playing: bool) -> None:
        self.playing_changed.emit(playing)

    @pyqtSlot(float)
    def setSpeed(self, speed: float) -> None:
        self.speed_changed.emit(speed)


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


def _json_plotly_value(value: Any) -> object:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value


class TelemetryPlotWidget(QWidget):
    index_selected = pyqtSignal(int)
    index_stepped = pyqtSignal(int)
    x_range_changed = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.log: LoadedLog | None = None
        self.compare: LoadedLog | None = None
        self.columns: list[str] = []
        self.selected_index = 0
        self.show_grid = True
        self.dark = True
        self.interaction_mode = "zoom"
        self.time_mode = "absolute"
        self.x_axis_range: tuple[object, object] | None = None
        self._reset_view_pending = False
        self._render_generation = 0
        self._loaded_render_generation = 0
        self._reset_target_generation = 0
        self.manual_axis_groups: list[tuple[str, ...]] = []
        self.ungrouped_axis_columns: set[str] = set()
        self._view: QWebEngineView | QTextEdit
        self._page: QWebEnginePage | None = None
        self._cursor_x_values: list[object] = []
        self._html_path: Path | None = None
        self._last_trace_point_budget = MAX_TELEMETRY_TRACE_POINTS_HARD_CAP
        self.setMinimumHeight(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._web_engine = _use_web_engine()
        if self._web_engine:
            self._view = QWebEngineView(self)
            page = QWebEnginePage(_persistent_web_profile(), self._view)
            settings = page.settings()
            if settings is None:
                raise RuntimeError("QWebEnginePage.settings() returned None")
            settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
            self._view.setPage(page)
            self._page = page
            page.loadFinished.connect(self._telemetry_page_loaded)
            self._bridge = _PlotBridge()
            self._bridge.index_selected.connect(self.index_selected)
            self._bridge.index_stepped.connect(self.index_stepped)
            self._bridge.x_range_changed.connect(self.x_range_changed)
            self._channel = QWebChannel(page)
            self._channel.registerObject("plotBridge", self._bridge)
            page.setWebChannel(self._channel)
        else:
            self._view = QTextEdit(self)
            self._view.setReadOnly(True)
        layout.addWidget(self._view)
        self._resize_render_timer = QTimer(self)
        self._resize_render_timer.setSingleShot(True)
        self._resize_render_timer.timeout.connect(self._maybe_render_after_resize)

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
        x_axis_range: tuple[object, object] | None = None,
        manual_axis_groups: Sequence[Sequence[str]] | None = None,
        ungrouped_axis_columns: Sequence[str] | None = None,
    ) -> None:
        self.log = log
        self.compare = compare
        self.columns = columns
        self.selected_index = selected_index
        self.show_grid = show_grid
        self.dark = dark
        if interaction_mode:
            self.interaction_mode = interaction_mode if interaction_mode in {"pan", "zoom"} else "zoom"
        if time_mode:
            self.time_mode = time_mode
        self.x_axis_range = x_axis_range
        if x_axis_range is not None:
            self._reset_view_pending = False
        self.manual_axis_groups = [tuple(group) for group in manual_axis_groups or ()]
        self.ungrouped_axis_columns = set(ungrouped_axis_columns or ())
        self._render()

    def set_interaction_mode(self, mode: str) -> None:
        mode = mode if mode in {"pan", "zoom"} else "zoom"
        self.interaction_mode = mode
        self._render()

    def set_time_mode(self, mode: str) -> None:
        self.time_mode = mode
        self._render()

    def set_x_axis_range(self, x_axis_range: tuple[object, object] | None) -> None:
        """Remember the live Plotly range without reloading its WebEngine page."""
        self.x_axis_range = x_axis_range
        if x_axis_range is not None:
            self._reset_view_pending = False

    @property
    def render_generation(self) -> int:
        return self._render_generation

    def reset_view(self) -> None:
        self.x_axis_range = None
        if not self._web_engine:
            self._render()
            return
        self._reset_view_pending = True
        self._reset_target_generation = self._render_generation
        self._apply_pending_reset()

    def _telemetry_page_loaded(self, success: bool) -> None:
        page = self._page
        if not success or page is None:
            return
        page.runJavaScript(
            "String(window.sloppyTelemetryRenderGeneration || '');",
            self._telemetry_page_generation_loaded,
        )

    def _telemetry_page_generation_loaded(self, result: object) -> None:
        try:
            generation = int(result) if isinstance(result, str) else -1
        except ValueError:
            return
        if generation != self._render_generation:
            return
        self._loaded_render_generation = generation
        self._apply_pending_reset()

    def _apply_pending_reset(self) -> None:
        if not self._reset_view_pending:
            return
        target_generation = self._reset_target_generation
        if self._loaded_render_generation != target_generation:
            return
        page = self._page
        if page is None:
            return
        page.runJavaScript(
            r"""
(function() {
  const plot = document.querySelector('.plotly-graph-div');
  if (!plot || !window.Plotly) {
    return false;
  }
  const generation = String(window.sloppyTelemetryRenderGeneration || '');
  const autorange = {'xaxis.autorange': true};
  Object.keys(plot.layout || {}).forEach((key) => {
    if (/^yaxis\d*$/.test(key)) {
      autorange[`${key}.autorange`] = true;
    }
  });
  Plotly.relayout(plot, autorange);
  return generation;
})();
""",
            lambda result: self._reset_view_completed(result, target_generation),
        )

    def _reset_view_completed(self, result: object, generation: int) -> None:
        if (
            result == str(generation)
            and generation == self._reset_target_generation
            and generation == self._loaded_render_generation
            and generation == self._render_generation
        ):
            self._reset_view_pending = False

    def set_cursor_index(self, selected_index: int) -> None:
        self.selected_index = selected_index
        if self.log is None:
            return
        if not self._web_engine:
            self._render()
            return
        page = self._page
        if page is None:
            return
        if not self._cursor_x_values:
            return
        safe_index = max(0, min(selected_index, len(self._cursor_x_values) - 1))
        x_value = _json_plotly_value(self._cursor_x_values[safe_index])
        script = f"""
(function() {{
  const plot = document.querySelector('.plotly-graph-div');
  if (!plot || !window.Plotly || !plot.layout || !Array.isArray(plot.layout.shapes) || !plot.layout.shapes.length) {{
    return false;
  }}
  const x = {json.dumps(x_value, allow_nan=False)};
  Plotly.relayout(plot, {{'shapes[0].x0': x, 'shapes[0].x1': x}});
  return true;
}})();
"""
        page.runJavaScript(script)

    def _render(self) -> None:
        # The Plotly figure is rebuilt on every state change so compare traces,
        # cursor selection, and display mode stay in sync with the main window.
        self._render_generation = getattr(self, "_render_generation", 0) + 1
        if self._web_engine and getattr(self, "_reset_view_pending", False):
            self._reset_target_generation = self._render_generation
        self._cursor_x_values = list(_telemetry_x_values(self.log, self.time_mode)) if self.log is not None else []
        trace_point_budget = self._current_trace_point_budget()
        manual_axis_groups = getattr(self, "manual_axis_groups", [])
        ungrouped_axis_columns = tuple(getattr(self, "ungrouped_axis_columns", ()))
        fig = build_telemetry_figure(
            self.log,
            self.columns,
            compare=self.compare,
            selected_index=self.selected_index,
            show_grid=self.show_grid,
            dark=self.dark,
            interaction_mode=self.interaction_mode,
            time_mode=self.time_mode,
            max_trace_points=trace_point_budget,
            x_axis_range=getattr(self, "x_axis_range", None),
            manual_axis_groups=manual_axis_groups,
            ungrouped_axis_columns=ungrouped_axis_columns,
        )
        self._last_trace_point_budget = trace_point_budget
        html = figure_html(fig, bridge=self._web_engine, dark=self.dark)
        if self._web_engine:
            marker = f"<script>window.sloppyTelemetryRenderGeneration = {self._render_generation};</script>"
            html = html.replace("<head>", f"<head>{marker}", 1)
            previous_path = self._html_path
            self._html_path = _write_temp_html(html, "telemetry-plot-")
            cast(QWebEngineView, self._view).setUrl(QUrl.fromLocalFile(str(self._html_path)))
            _remove_file(previous_path)
        else:
            self._view.setHtml(html)

    def _current_trace_point_budget(self) -> int:
        try:
            view_width = int(self._view.width())
        except (AttributeError, RuntimeError, TypeError):
            return MAX_TELEMETRY_TRACE_POINTS_HARD_CAP
        axis_group_count = _telemetry_axis_group_count(
            getattr(self, "log", None),
            self.columns,
            manual_axis_groups=getattr(self, "manual_axis_groups", []),
            ungrouped_axis_columns=tuple(getattr(self, "ungrouped_axis_columns", ())),
        )
        plotted_axis_count = min(axis_group_count, MAX_RENDERED_TELEMETRY_TRACES)
        plot_width = view_width - TELEMETRY_MARGIN_LEFT - _telemetry_right_margin(plotted_axis_count)
        return _telemetry_trace_point_budget(max(1, plot_width))

    def _trace_point_budget_changed(self, next_budget: int) -> bool:
        threshold = max(500, int(self._last_trace_point_budget * 0.10))
        return abs(next_budget - self._last_trace_point_budget) >= threshold

    def _schedule_resize_render(self) -> None:
        if self.log is None or not self.columns:
            return
        next_budget = self._current_trace_point_budget()
        if not self._trace_point_budget_changed(next_budget):
            return
        self._resize_render_timer.start(TELEMETRY_RESIZE_RERENDER_MS)

    def _maybe_render_after_resize(self) -> None:
        if self.log is None or not self.columns:
            return
        if self._trace_point_budget_changed(self._current_trace_point_budget()):
            self._render()

    def resizeEvent(self, a0) -> None:
        super().resizeEvent(a0)
        self._schedule_resize_render()

    def closeEvent(self, a0) -> None:
        _remove_file(self._html_path)
        self._html_path = None
        super().closeEvent(a0)


class GpsPathWidget(QWidget):
    elapsed_seeked = pyqtSignal(float)
    playing_changed = pyqtSignal(bool)
    speed_changed = pyqtSignal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.log: LoadedLog | None = None
        self.options = GpsGradientOptions()
        self.dark = True
        self._last_cursor: dict[str, Any] | None = None
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
            self._bridge = _GpsBridge()
            self._bridge.elapsed_seeked.connect(self.elapsed_seeked.emit)
            self._bridge.playing_changed.connect(self.playing_changed.emit)
            self._bridge.speed_changed.connect(self.speed_changed.emit)
            self._channel = QWebChannel(self._page)
            self._channel.registerObject("gpsBridge", self._bridge)
            self._page.setWebChannel(self._channel)
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

    def set_cursor(self, cursor: dict[str, Any]) -> None:
        self._last_cursor = dict(cursor)
        if not self._web_engine:
            return
        page = self._page
        if page is None:
            return
        data = json.dumps(cursor, allow_nan=False)
        page.runJavaScript(f"window.sloppyGpsMap ? window.sloppyGpsMap.setCursor({data}) : false;")

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
            if self._last_cursor is not None:
                self.set_cursor(self._last_cursor)

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
