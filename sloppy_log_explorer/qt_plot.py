"""Qt widgets that render Plotly graphs and the GPS map."""

from __future__ import annotations

from collections.abc import Sequence
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, cast

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal, pyqtSlot
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QTextEdit, QVBoxLayout, QWidget

from .map_basemaps import (
    BASEMAP_OPENSTREETMAP,
    clamp_imagery_opacity,
    normalize_basemap,
)
from .models import GpsGradientOptions, LoadedLog
from .reception_map_renderer import build_reception_map_html
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


def _new_local_map_page(view: QWebEngineView) -> QWebEnginePage:
    """Create a map page with the shared persistent cache and local-file access."""
    page = QWebEnginePage(_persistent_web_profile(), view)
    settings = page.settings()
    if settings is None:
        raise RuntimeError("QWebEnginePage.settings() returned None")
    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
    view.setPage(page)
    return page


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
        self.basemap = BASEMAP_OPENSTREETMAP
        self.imagery_opacity = 1.0
        self._last_cursor: dict[str, Any] | None = None
        self._view: QWebEngineView | QTextEdit
        self._html_path: Path | None = None
        self._page: QWebEnginePage | None = None
        self._render_generation = 0
        self._loaded_render_generation = -1
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
            self._page = _new_local_map_page(self._view)
            self._bridge = _GpsBridge()
            self._bridge.elapsed_seeked.connect(self.elapsed_seeked.emit)
            self._bridge.playing_changed.connect(self.playing_changed.emit)
            self._bridge.speed_changed.connect(self.speed_changed.emit)
            self._channel = QWebChannel(self._page)
            self._channel.registerObject("gpsBridge", self._bridge)
            self._page.setWebChannel(self._channel)
            self._page.loadFinished.connect(self._map_page_loaded)
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
        try:
            previous_generation = self._render_generation
        except (AttributeError, RuntimeError):
            previous_generation = 0
        try:
            basemap = self.basemap
        except (AttributeError, RuntimeError):
            basemap = BASEMAP_OPENSTREETMAP
        try:
            imagery_opacity = self.imagery_opacity
        except (AttributeError, RuntimeError):
            imagery_opacity = 1.0
        self._render_generation = previous_generation + 1
        self._loaded_render_generation = -1
        html = build_gps_map_html(
            log,
            options=self.options,
            dark=dark,
            basemap=normalize_basemap(basemap),
            imagery_opacity=clamp_imagery_opacity(imagery_opacity),
        )
        if self._web_engine:
            marker = (
                "<script>window.sloppyGpsRenderGeneration = "
                f"{self._render_generation};</script>"
            )
            html = html.replace("<head>", f"<head>{marker}", 1)
            # Regenerate the HTML file on each refresh so the embedded browser
            # always reads the latest payload and can access local assets.
            previous_path = self._html_path
            self._html_path = _write_temp_html(html, "gps-map-")
            cast(QWebEngineView, self._view).setUrl(QUrl.fromLocalFile(str(self._html_path)))
            _remove_file(previous_path)
            self._schedule_viewport_refresh(fit=True)
        else:
            self._view.setHtml(html)

    def set_basemap(self, basemap: str) -> None:
        """Switch the live GPS basemap without replacing the map document."""
        self.basemap = normalize_basemap(basemap)
        if (
            not self._web_engine
            or self._page is None
            or self._loaded_render_generation != self._render_generation
        ):
            return
        generation = self._render_generation
        value = json.dumps(self.basemap)
        self._page.runJavaScript(
            "(() => {"
            f"if (window.sloppyGpsRenderGeneration !== {generation}) return false;"
            "const api = window.sloppyGpsMap;"
            "return api && typeof api.setBasemap === 'function' "
            f"? api.setBasemap({value}) : false;"
            "})();"
        )

    def set_imagery_opacity(self, opacity: float) -> None:
        """Update GPS imagery opacity without disturbing camera or playback."""
        self.imagery_opacity = clamp_imagery_opacity(opacity)
        if (
            not self._web_engine
            or self._page is None
            or self._loaded_render_generation != self._render_generation
        ):
            return
        generation = self._render_generation
        value = json.dumps(self.imagery_opacity)
        self._page.runJavaScript(
            "(() => {"
            f"if (window.sloppyGpsRenderGeneration !== {generation}) return false;"
            "const api = window.sloppyGpsMap;"
            "return api && typeof api.setImageryOpacity === 'function' "
            f"? api.setImageryOpacity({value}) : false;"
            "})();"
        )

    def _reapply_basemap_controls(self) -> None:
        GpsPathWidget.set_basemap(
            self,
            getattr(self, "basemap", BASEMAP_OPENSTREETMAP),
        )
        GpsPathWidget.set_imagery_opacity(
            self,
            getattr(self, "imagery_opacity", 1.0),
        )

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
        page = self._page
        if not ok or page is None:
            return
        page.runJavaScript(
            "String(window.sloppyGpsRenderGeneration || '');",
            self._map_page_generation_loaded,
        )

    def _map_page_generation_loaded(self, result: object) -> None:
        try:
            generation = int(result) if isinstance(result, str) else -1
        except ValueError:
            return
        if generation != self._render_generation:
            return
        self._loaded_render_generation = generation
        GpsPathWidget._reapply_basemap_controls(self)
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


class ReceptionMapWidget(QWidget):
    """Qt WebEngine host for a generated multi-flight reception map."""

    ready_changed = pyqtSignal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.payload: dict[str, object] | None = None
        self.dark = True
        self.opacity = 0.72
        self.basemap = BASEMAP_OPENSTREETMAP
        self.imagery_opacity = 1.0
        self._view: QWebEngineView | QTextEdit
        self._html_path: Path | None = None
        self._page: QWebEnginePage | None = None
        self._ready = False
        self._ready_state: dict[str, object] | None = None
        self._render_generation = 0
        self._loaded_render_generation = -1
        self._ready_poll_attempts = 0
        self._ready_poll_timer = QTimer(self)
        self._ready_poll_timer.setSingleShot(True)
        self._ready_poll_timer.timeout.connect(self._poll_ready_state)
        self._pending_viewport_fit = False
        self._viewport_refresh_timer = QTimer(self)
        self._viewport_refresh_timer.setSingleShot(True)
        self._viewport_refresh_timer.timeout.connect(self._run_scheduled_viewport_refresh)
        self._auto_fitted_generation = -1
        self.setMinimumHeight(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._web_engine = _use_web_engine()
        if self._web_engine:
            self._view = QWebEngineView(self)
            self._page = _new_local_map_page(self._view)
            self._page.loadFinished.connect(self._map_page_loaded)
        else:
            self._view = QTextEdit(self)
            self._view.setReadOnly(True)
        layout.addWidget(self._view)

    @property
    def ready(self) -> bool:
        """Whether the current WebEngine document reports its map layers ready."""
        return self._ready

    @property
    def ready_state(self) -> dict[str, object] | None:
        """Return the latest JSON-safe state reported by the map document."""
        return dict(self._ready_state) if self._ready_state is not None else None

    def _set_ready(self, ready: bool) -> None:
        ready = bool(ready)
        if ready == self._ready:
            return
        self._ready = ready
        self.ready_changed.emit(ready)

    def set_heatmap(self, payload: dict[str, object] | None, dark: bool = True) -> None:
        """Render a JSON-safe reception payload, replacing the previous map."""
        self.payload = dict(payload) if payload is not None else None
        if self.payload is not None:
            fallback_opacity = float(getattr(self, "opacity", 0.72))
            raw_opacity = self.payload.get("opacity", fallback_opacity)
            try:
                opacity = float(raw_opacity) if isinstance(raw_opacity, (int, float, str)) else fallback_opacity
            except (TypeError, ValueError):
                opacity = fallback_opacity
            self.opacity = max(0.0, min(1.0, opacity))
            self.basemap = normalize_basemap(
                self.payload.get("basemap", getattr(self, "basemap", BASEMAP_OPENSTREETMAP))
            )
            self.imagery_opacity = clamp_imagery_opacity(
                self.payload.get(
                    "imagery_opacity",
                    self.payload.get("imageryOpacity", getattr(self, "imagery_opacity", 1.0)),
                )
            )
        self.dark = dark
        self._render_generation += 1
        self._loaded_render_generation = -1
        self._ready_poll_timer.stop()
        self._ready_poll_attempts = 0
        self._ready_state = None
        self._auto_fitted_generation = -1
        self._set_ready(False)
        render_payload = dict(self.payload) if self.payload is not None else None
        if render_payload is not None:
            render_payload["basemap"] = self.basemap
            render_payload["imagery_opacity"] = self.imagery_opacity
        html_document = build_reception_map_html(render_payload, dark=dark)
        if self._web_engine:
            marker = (
                "<script>window.sloppyReceptionRenderGeneration = "
                f"{self._render_generation};</script>"
            )
            html_document = html_document.replace("<head>", f"<head>{marker}", 1)
            previous_path = self._html_path
            self._html_path = _write_temp_html(html_document, "reception-map-")
            cast(QWebEngineView, self._view).setUrl(QUrl.fromLocalFile(str(self._html_path)))
            _remove_file(previous_path)
            ReceptionMapWidget._schedule_viewport_refresh(self, fit=True)
        else:
            cast(QTextEdit, self._view).setHtml(html_document)

    def set_opacity(self, opacity: float) -> None:
        """Update reception cell opacity without rebuilding the map document."""
        try:
            numeric = float(opacity)
        except (TypeError, ValueError):
            numeric = self.opacity
        self.opacity = max(0.0, min(1.0, numeric))
        if self.payload is not None:
            self.payload["opacity"] = self.opacity
        if (
            not self._web_engine
            or self._page is None
            or self._loaded_render_generation != self._render_generation
        ):
            return
        generation = self._render_generation
        value = json.dumps(self.opacity)
        self._page.runJavaScript(
            "(() => {"
            f"if (window.sloppyReceptionRenderGeneration !== {generation}) return false;"
            "const api = window.sloppyReceptionMap;"
            "return api && typeof api.setOpacity === 'function' "
            f"? api.setOpacity({value}) : false;"
            "})();"
        )

    def set_color_scale(
        self,
        auto_range: bool,
        minimum: float,
        maximum: float,
        reverse: bool,
    ) -> None:
        """Update reception colors and legend without rebuilding the map document."""
        minimum_value = float(minimum)
        maximum_value = float(maximum)
        if not math.isfinite(minimum_value) or not math.isfinite(maximum_value):
            return
        options = {
            "autoRange": bool(auto_range),
            "minimum": minimum_value,
            "maximum": maximum_value,
            "reverse": bool(reverse),
        }
        if self.payload is not None:
            self.payload["auto_range"] = bool(auto_range)
            self.payload["reverse"] = bool(reverse)
            if auto_range:
                self.payload.pop("range_min", None)
                self.payload.pop("range_max", None)
            else:
                self.payload["range_min"] = minimum_value
                self.payload["range_max"] = maximum_value
        if (
            not self._web_engine
            or self._page is None
            or self._loaded_render_generation != self._render_generation
        ):
            return
        generation = self._render_generation
        data = json.dumps(options, allow_nan=False, separators=(",", ":"))
        self._page.runJavaScript(
            "(() => {"
            f"if (window.sloppyReceptionRenderGeneration !== {generation}) return false;"
            "const api = window.sloppyReceptionMap;"
            "return api && typeof api.setColorScale === 'function' "
            f"? api.setColorScale({data}) : false;"
            "})();"
        )

    def _reapply_live_controls(self) -> None:
        """Apply the latest controls after the current map document loads."""
        ReceptionMapWidget.set_basemap(
            self,
            getattr(self, "basemap", BASEMAP_OPENSTREETMAP),
        )
        ReceptionMapWidget.set_imagery_opacity(
            self,
            getattr(self, "imagery_opacity", 1.0),
        )
        ReceptionMapWidget.set_opacity(self, self.opacity)
        payload = self.payload
        if payload is None:
            return
        auto_range = payload.get("auto_range", True) is not False
        minimum_key = "value_min" if auto_range else "range_min"
        maximum_key = "value_max" if auto_range else "range_max"
        raw_minimum = payload.get(minimum_key, 0.0)
        raw_maximum = payload.get(maximum_key, 1.0)
        try:
            minimum = float(raw_minimum) if isinstance(raw_minimum, (int, float, str)) else 0.0
            maximum = float(raw_maximum) if isinstance(raw_maximum, (int, float, str)) else 1.0
        except (TypeError, ValueError):
            minimum, maximum = 0.0, 1.0
        if not math.isfinite(minimum) or not math.isfinite(maximum):
            minimum, maximum = 0.0, 1.0
        ReceptionMapWidget.set_color_scale(
            self,
            auto_range,
            minimum,
            maximum,
            bool(payload.get("reverse", False)),
        )

    def set_basemap(self, basemap: str) -> None:
        """Switch the live reception basemap without rebuilding the heatmap."""
        self.basemap = normalize_basemap(basemap)
        if self.payload is not None:
            self.payload["basemap"] = self.basemap
        if (
            not self._web_engine
            or self._page is None
            or self._loaded_render_generation != self._render_generation
        ):
            return
        generation = self._render_generation
        value = json.dumps(self.basemap)
        self._page.runJavaScript(
            "(() => {"
            f"if (window.sloppyReceptionRenderGeneration !== {generation}) return false;"
            "const api = window.sloppyReceptionMap;"
            "return api && typeof api.setBasemap === 'function' "
            f"? api.setBasemap({value}) : false;"
            "})();"
        )

    def set_imagery_opacity(self, opacity: float) -> None:
        """Update imagery opacity without changing reception cell opacity."""
        self.imagery_opacity = clamp_imagery_opacity(opacity)
        if self.payload is not None:
            self.payload["imagery_opacity"] = self.imagery_opacity
        if (
            not self._web_engine
            or self._page is None
            or self._loaded_render_generation != self._render_generation
        ):
            return
        generation = self._render_generation
        value = json.dumps(self.imagery_opacity)
        self._page.runJavaScript(
            "(() => {"
            f"if (window.sloppyReceptionRenderGeneration !== {generation}) return false;"
            "const api = window.sloppyReceptionMap;"
            "return api && typeof api.setImageryOpacity === 'function' "
            f"? api.setImageryOpacity({value}) : false;"
            "})();"
        )

    def refresh_viewport(self, fit: bool = False) -> None:
        """Resize the live map and optionally restore its density-focused camera."""
        if not self._web_engine or self._page is None:
            return
        options = json.dumps({"fit": fit})
        self._page.runJavaScript(
            f"window.sloppyReceptionMap ? window.sloppyReceptionMap.refresh({options}) : false;"
        )

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
        page = self._page
        if not ok or page is None:
            return
        page.runJavaScript(
            "window.sloppyReceptionMap "
            "? String(window.sloppyReceptionRenderGeneration || '') : '';",
            self._map_page_generation_loaded,
        )

    def _map_page_generation_loaded(self, result: object) -> None:
        try:
            generation = int(result) if isinstance(result, str) else -1
        except ValueError:
            return
        if generation != self._render_generation:
            return
        self._loaded_render_generation = generation
        ReceptionMapWidget._reapply_live_controls(self)
        self._schedule_viewport_refresh(fit=True)
        self._ready_poll_attempts = 0
        self._poll_ready_state()

    def _poll_ready_state(self) -> None:
        page = self._page
        if not self._web_engine or page is None:
            return
        generation = self._render_generation
        page.runJavaScript(
            "window.sloppyReceptionMap && window.sloppyReceptionMap.getState "
            "? window.sloppyReceptionMap.getState() : null;",
            lambda result: self._ready_state_received(result, generation),
        )

    def _ready_state_received(self, result: object, generation: int) -> None:
        if generation != self._render_generation:
            return
        state = cast(dict[str, object], result) if isinstance(result, dict) else None
        self._ready_state = dict(state) if state is not None else None
        if (
            state is not None
            and state.get("state") == "ready"
            and getattr(self, "_auto_fitted_generation", -1) != generation
        ):
            self._auto_fitted_generation = generation
            ReceptionMapWidget._schedule_viewport_refresh(self, fit=True)
        if state is not None and bool(state.get("ready")):
            self._set_ready(True)
            return
        self._set_ready(False)
        if state is not None and state.get("state") in {"error", "empty"}:
            return
        self._ready_poll_attempts += 1
        if self._ready_poll_attempts < 150:
            self._ready_poll_timer.start(100)

    def showEvent(self, a0) -> None:
        super().showEvent(a0)
        self._schedule_viewport_refresh(fit=True)

    def resizeEvent(self, a0) -> None:
        super().resizeEvent(a0)
        # Recompute the zoom floor for the new canvas dimensions so the
        # automatic reception view cannot grow beyond its 2 km radius cap.
        self._schedule_viewport_refresh(fit=True)

    def closeEvent(self, a0) -> None:
        self._ready_poll_timer.stop()
        self._viewport_refresh_timer.stop()
        _remove_file(self._html_path)
        self._html_path = None
        super().closeEvent(a0)
