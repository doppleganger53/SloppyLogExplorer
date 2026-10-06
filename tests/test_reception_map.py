from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from PyQt6.QtCore import QSize
from PyQt6.QtGui import QResizeEvent
from PyQt6.QtWidgets import QApplication, QTextEdit

from sloppy_log_explorer.qt_plot import ReceptionMapWidget
from sloppy_log_explorer.reception_map_renderer import build_reception_map_html


def sample_payload() -> dict[str, object]:
    return {
        "status": "ok",
        "site_name": "West Jersey RC Club",
        "telemetry_column": "VFR 2.4G(%)",
        "cell_size_m": 5,
        "auto_range": True,
        "reverse": False,
        "viewport_focus": {
            "latitude": 39.7744,
            "longitude": -75.2049,
            "radius_m": 2_000,
            "cell_count": 2,
            "sample_count": 27,
            "flight_count": 5,
        },
        "cells": [
            {
                "latitude": 39.774389,
                "longitude": -75.204944,
                "value": 92.5,
                "sample_count": 18,
                "flight_count": 3,
            },
            {
                "latitude": 39.77444,
                "longitude": -75.20488,
                "value": 47.0,
                "sample_count": 9,
                "flight_count": 2,
            },
        ],
    }


def test_reception_renderer_handles_initial_empty_and_error_states() -> None:
    initial = build_reception_map_html(None)
    empty = build_reception_map_html({"status": "ok", "cells": []}, dark=False)
    error = build_reception_map_html(
        {"status": "error", "message": "Could not read <flight.csv>"}, dark=True
    )

    assert "Generate a reception map" in initial
    assert 'state: "empty"' in initial
    assert "No reception samples matched" in empty
    assert "Could not read &lt;flight.csv&gt;" in error
    assert 'state: "error"' in error
    cancelled = build_reception_map_html(
        {"status": "cancelled", "message": "Generation cancelled", "cells": []}
    )
    assert 'state: "empty"' in cancelled


@pytest.mark.parametrize("latitude", [85.05112878, 89.0, -85.05112878])
def test_reception_renderer_rejects_focus_at_or_beyond_web_mercator_limit(
    latitude: float,
) -> None:
    payload = sample_payload()
    focus = cast(dict[str, object], payload["viewport_focus"])
    focus["latitude"] = latitude

    document = build_reception_map_html(payload)

    assert "at or beyond the Web Mercator display limit" in document
    assert 'state: "error"' in document


def test_reception_renderer_requires_density_focus_for_nonempty_map() -> None:
    payload = sample_payload()
    payload.pop("viewport_focus")

    document = build_reception_map_html(payload)

    assert "missing density-focus metadata" in document
    assert 'state: "error"' in document


def test_reception_renderer_uses_vendored_maplibre_and_osm_raster_cells() -> None:
    document = build_reception_map_html(sample_payload(), dark=True)

    assert "assets/maplibre/maplibre-gl.css" in document.replace("\\", "/")
    assert "assets/maplibre/maplibre-gl.mjs" in document.replace("\\", "/")
    assert 'script type="module"' in document
    assert "import * as maplibregl" in document
    assert "https://tile.openstreetmap.org/{z}/{x}/{y}.png" in document
    assert "imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPImagery" in document
    assert "gibs.earthdata.nasa.gov/wmts/epsg3857" in document
    assert 'id: "osm-raster-base"' in document
    assert 'id: "nasa-gibs-raster-base"' in document
    assert 'id: "usgs-naip-raster-base"' in document
    assert "OpenStreetMap contributors" in document
    assert "USGS/USDA NAIP" in document
    assert "NASA GIBS" in document
    assert 'id: "reception-cells-fill"' in document
    assert 'id: "reception-cells-outline"' in document
    assert 'source: "reception-cells"' in document
    assert "defaultCellSizeMeters = 5" in document
    assert "observed ${formatValue(configuredCellSizeMeters())} m cells" in document
    assert '["Samples"' in document
    assert '["Flights"' in document
    assert "pitch: 0" in document
    assert "bearing: 0" in document
    assert "const viewportMaxRadiusMeters = 2000" in document
    assert "function minimumZoomForRadius" in document
    assert "function minimumZoomForMercatorEdge" in document
    assert "finalRadius > viewportFocus.radiusMeters + 0.5" in document
    assert "viewportRadiusMeters: visibleViewportRadiusMeters(viewportFocus)" in document
    assert "map.jumpTo" in document
    assert "window.sloppyReceptionMap" in document
    assert "setColorScale," in document
    assert "setBasemap," in document
    assert "setImageryOpacity," in document
    assert "basemap: basemapDebugState()" in document
    assert "if (handleBasemapError(event)) return;" in document
    assert 'map.on("style.load", reapplyBasemapState)' in document
    assert 'map.setPaintProperty("reception-cells-fill", "fill-color", colorExpression(range))' in document
    assert 'map.once("idle", () => refreshMapViewport({fit: false}))' in document
    assert "accessToken" not in document


def test_reception_renderer_uses_configured_cell_size_in_legend() -> None:
    payload = sample_payload()
    payload["cell_size_m"] = 12.5

    document = build_reception_map_html(payload)

    assert '"cell_size_m":12.5' in document
    assert '["cell_size_m", "cellSizeMeters"]' in document
    assert "observed ${formatValue(configuredCellSizeMeters())} m cells" in document


def test_reception_renderer_preserves_manual_range_and_reverse_setting() -> None:
    payload = sample_payload()
    payload.update(
        {
            "auto_range": False,
            "range_min": 10.0,
            "range_max": 100.0,
            "reverse": True,
        }
    )

    document = build_reception_map_html(payload)

    assert '"auto_range":false' in document
    assert '"range_min":10.0' in document
    assert '"range_max":100.0' in document
    assert '"reverse":true' in document
    assert 'value === null || value === undefined' in document
    assert 'range.reverse ? "#16a34a" : "#dc2626"' in document
    assert 'classList.toggle("reverse", range.reverse)' in document


def test_reception_renderer_exposes_live_opacity_and_normalization_label() -> None:
    payload = sample_payload()
    payload["opacity"] = 0.35
    payload["telemetry_label"] = "VFR 900M(%) ÷ clamp(Power 900M(mW), 10, 500)"

    document = build_reception_map_html(payload)

    assert '"opacity":0.35' in document
    assert r"VFR 900M(%) \u00f7 clamp(Power 900M(mW), 10, 500)" in document
    assert '"fill-opacity": heatmapOpacity' in document
    assert '"line-opacity": outlineOpacity()' in document
    assert 'map.setPaintProperty("reception-cells-fill", "fill-opacity", heatmapOpacity)' in document
    assert 'map.setPaintProperty("reception-cells-outline", "line-opacity", outlineOpacity())' in document
    assert "setOpacity," in document
    assert "opacity: heatmapOpacity" in document


def test_reception_renderer_keeps_imagery_and_heatmap_opacity_independent() -> None:
    payload = sample_payload()
    payload.update(
        {
            "basemap": "imagery",
            "imagery_opacity": 0.45,
            "opacity": 0.72,
        }
    )

    document = build_reception_map_html(payload, dark=False)

    assert '"selected":"imagery"' in document
    assert '"imageryOpacity":0.45' in document
    assert '"opacity":0.72' in document
    assert "let imageryOpacity = clampBasemapOpacity(basemapConfig.imageryOpacity)" in document
    assert "let heatmapOpacity = clamp(" in document
    assert 'paint: {"background-color": "#ffffff"}' in document
    assert "__BASEMAP_" not in document


def test_reception_renderer_accepts_polygon_and_bounds_cells() -> None:
    payload = sample_payload()
    payload["cells"] = [
        {
            "polygon": [
                [-75.2050, 39.7743],
                [-75.2049, 39.7743],
                [-75.2049, 39.7744],
                [-75.2050, 39.7744],
            ],
            "value": 80,
            "sampleCount": 4,
            "logCount": 2,
        },
        {
            "bounds": [-75.2048, 39.7743, -75.2047, 39.7744],
            "value": 30,
            "samples": 6,
            "flights": 1,
        },
    ]

    document = build_reception_map_html(payload)

    assert '"polygon":[[-75.205,39.7743]' in document
    assert '"bounds":[-75.2048,39.7743,-75.2047,39.7744]' in document
    assert '["flight_count", "flightCount", "log_count", "logCount", "logs"]' in document


def test_reception_renderer_uses_strict_script_safe_json() -> None:
    payload = sample_payload()
    payload["telemetry_column"] = "RSSI </script><script>window.injected=true</script>"

    document = build_reception_map_html(payload)

    assert "RSSI </script><script>window.injected=true</script>" not in document
    assert r"RSSI \u003c/script>\u003cscript>window.injected=true\u003c/script>" in document

    cells = cast(list[dict[str, object]], payload["cells"])
    cells[0]["value"] = float("nan")
    with pytest.raises(ValueError, match="Out of range float"):
        build_reception_map_html(payload)


def test_reception_widget_offscreen_keeps_payload_and_empty_ready_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    widget = ReceptionMapWidget()

    assert isinstance(widget._view, QTextEdit)
    assert widget.payload is None
    assert not widget.ready
    assert widget._view.toPlainText() == ""

    payload = sample_payload()
    widget.set_heatmap(payload, dark=False)

    assert widget.payload == payload
    assert not widget.ready
    assert widget.dark is False
    assert widget._html_path is None
    assert widget._view.toPlainText()
    widget.close()
    app.processEvents()


def test_reception_widget_resize_requests_density_focused_refit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    widget = ReceptionMapWidget()
    requested_fits: list[bool] = []
    monkeypatch.setattr(
        widget,
        "_schedule_viewport_refresh",
        lambda fit=False: requested_fits.append(fit),
    )

    widget.resizeEvent(QResizeEvent(QSize(1_280, 820), QSize(800, 600)))

    assert requested_fits == [True]
    widget.close()
    app.processEvents()


def test_reception_widget_webengine_replaces_temp_document(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    old_path = tmp_path / "old.html"
    new_path = tmp_path / "new.html"
    old_path.write_text("old", encoding="utf-8")
    urls: list[object] = []
    removed: list[Path | None] = []

    class FakeView:
        def setUrl(self, url: object) -> None:
            urls.append(url)

    class FakeTimer:
        def stop(self) -> None:
            pass

    fake_widget = cast(Any, type("FakeReceptionWidget", (), {})())
    fake_widget.payload = None
    fake_widget.dark = True
    fake_widget._view = FakeView()
    fake_widget._html_path = old_path
    fake_widget._web_engine = True
    fake_widget._render_generation = 2
    fake_widget._loaded_render_generation = 2
    fake_widget._ready_poll_timer = FakeTimer()
    fake_widget._ready_poll_attempts = 4
    fake_widget._ready_state = {"ready": True}
    fake_widget._pending_viewport_fit = False
    fake_widget._auto_fitted_generation = 2
    fake_widget._set_ready = lambda value: setattr(fake_widget, "_ready", value)

    class ViewportTimer:
        def isActive(self) -> bool:
            return False

        def start(self, _milliseconds: int) -> None:
            pass

    fake_widget._viewport_refresh_timer = ViewportTimer()

    documents: list[str] = []
    monkeypatch.setattr(
        "sloppy_log_explorer.qt_plot.build_reception_map_html",
        lambda *_args, **_kwargs: "<html><head></head><body></body></html>",
    )
    monkeypatch.setattr(
        "sloppy_log_explorer.qt_plot._write_temp_html",
        lambda document, *_args, **_kwargs: (documents.append(document), new_path)[1],
    )
    monkeypatch.setattr("sloppy_log_explorer.qt_plot._remove_file", removed.append)

    ReceptionMapWidget.set_heatmap(fake_widget, sample_payload(), dark=False)

    assert fake_widget._render_generation == 3
    assert fake_widget._loaded_render_generation == -1
    assert fake_widget._html_path == new_path
    assert fake_widget._ready is False
    assert fake_widget._ready_state is None
    assert fake_widget._pending_viewport_fit is True
    assert fake_widget._auto_fitted_generation == -1
    assert removed == [old_path]
    assert urls
    assert "window.sloppyReceptionRenderGeneration = 3" in documents[0]


def test_reception_widget_refreshes_and_tracks_reported_ready_state() -> None:
    scripts: list[str] = []

    class FakePage:
        def runJavaScript(self, script: str, callback=None) -> None:
            scripts.append(script)
            if callback is not None:
                callback(
                    {
                        "state": "ready",
                        "ready": True,
                        "cellCount": 2,
                        "layers": {"source": True, "fill": True, "outline": True},
                    }
                )

    class FakeTimer:
        def start(self, _milliseconds: int) -> None:
            raise AssertionError("ready state should not schedule another poll")

    fake_widget = cast(Any, type("FakeReceptionWidget", (), {})())
    fake_widget._web_engine = True
    fake_widget._page = FakePage()
    fake_widget._render_generation = 7
    fake_widget._ready_poll_attempts = 0
    fake_widget._ready_poll_timer = FakeTimer()
    fake_widget._ready_state = None
    fake_widget._ready = False
    fake_widget._pending_viewport_fit = False
    fake_widget._auto_fitted_generation = -1

    class ViewportTimer:
        def __init__(self) -> None:
            self.started = 0

        def isActive(self) -> bool:
            return False

        def start(self, _milliseconds: int) -> None:
            self.started += 1

    viewport_timer = ViewportTimer()
    fake_widget._viewport_refresh_timer = viewport_timer
    fake_widget._set_ready = lambda value: setattr(fake_widget, "_ready", value)
    fake_widget._ready_state_received = (
        lambda result, generation: ReceptionMapWidget._ready_state_received(
            fake_widget, result, generation
        )
    )

    ReceptionMapWidget.refresh_viewport(fake_widget, fit=True)
    ReceptionMapWidget._poll_ready_state(fake_widget)

    assert '"fit": true' in scripts[0]
    assert "window.sloppyReceptionMap.getState" in scripts[1]
    assert fake_widget._ready
    assert fake_widget._ready_state is not None
    assert fake_widget._ready_state["cellCount"] == 2
    assert fake_widget._pending_viewport_fit is True
    assert fake_widget._auto_fitted_generation == 7
    assert viewport_timer.started == 1


def test_reception_widget_failed_load_does_not_clear_current_ready_state() -> None:
    class FakePage:
        def runJavaScript(self, _script: str, _callback=None) -> None:
            raise AssertionError("failed loads must not inspect or mutate the current document")

    fake_widget = cast(Any, type("FakeReceptionWidget", (), {})())
    fake_widget._page = FakePage()
    fake_widget._ready = True
    ready_updates: list[bool] = []
    fake_widget._set_ready = ready_updates.append

    ReceptionMapWidget._map_page_loaded(fake_widget, False)

    assert fake_widget._ready is True
    assert ready_updates == []


def test_reception_widget_updates_opacity_without_replacing_document() -> None:
    scripts: list[str] = []

    class FakePage:
        def runJavaScript(self, script: str, callback=None) -> None:
            scripts.append(script)

    fake_widget = cast(Any, type("FakeReceptionWidget", (), {})())
    fake_widget.opacity = 0.72
    fake_widget.payload = sample_payload()
    fake_widget._web_engine = True
    fake_widget._page = FakePage()
    fake_widget._render_generation = 7
    fake_widget._loaded_render_generation = 7

    ReceptionMapWidget.set_opacity(fake_widget, 0.35)

    assert fake_widget.opacity == 0.35
    assert fake_widget.payload["opacity"] == 0.35
    assert len(scripts) == 1
    assert "window.sloppyReceptionRenderGeneration !== 7" in scripts[0]
    assert "typeof api.setOpacity === 'function'" in scripts[0]
    assert "api.setOpacity(0.35)" in scripts[0]

    ReceptionMapWidget.set_opacity(fake_widget, 2.0)
    assert fake_widget.opacity == 1.0
    assert "api.setOpacity(1.0)" in scripts[-1]


def test_reception_widget_updates_basemap_without_replacing_document() -> None:
    scripts: list[str] = []

    class FakePage:
        def runJavaScript(self, script: str, callback=None) -> None:
            scripts.append(script)

    fake_widget = cast(Any, type("FakeReceptionWidget", (), {})())
    fake_widget.payload = sample_payload()
    fake_widget.payload["opacity"] = 0.72
    fake_widget._web_engine = True
    fake_widget._page = FakePage()
    fake_widget._render_generation = 6
    fake_widget._loaded_render_generation = 6
    fake_widget.basemap = "osm"
    fake_widget.imagery_opacity = 1.0

    ReceptionMapWidget.set_basemap(fake_widget, "imagery")
    ReceptionMapWidget.set_imagery_opacity(fake_widget, 0.35)

    assert fake_widget.basemap == "imagery"
    assert fake_widget.imagery_opacity == 0.35
    assert fake_widget.payload["basemap"] == "imagery"
    assert fake_widget.payload["imagery_opacity"] == 0.35
    assert fake_widget.payload["opacity"] == 0.72
    assert len(scripts) == 2
    assert "window.sloppyReceptionRenderGeneration !== 6" in scripts[0]
    assert 'api.setBasemap("imagery")' in scripts[0]
    assert "api.setImageryOpacity(0.35)" in scripts[1]


def test_reception_widget_updates_color_scale_without_replacing_document() -> None:
    scripts: list[str] = []

    class FakePage:
        def runJavaScript(self, script: str, callback=None) -> None:
            scripts.append(script)

    fake_widget = cast(Any, type("FakeReceptionWidget", (), {})())
    fake_widget.payload = sample_payload()
    fake_widget._web_engine = True
    fake_widget._page = FakePage()
    fake_widget._render_generation = 4
    fake_widget._loaded_render_generation = 4

    ReceptionMapWidget.set_color_scale(fake_widget, False, 2.0, 85.0, True)

    assert fake_widget.payload["auto_range"] is False
    assert fake_widget.payload["range_min"] == 2.0
    assert fake_widget.payload["range_max"] == 85.0
    assert fake_widget.payload["reverse"] is True
    assert len(scripts) == 1
    assert "window.sloppyReceptionRenderGeneration !== 4" in scripts[0]
    assert "typeof api.setColorScale === 'function'" in scripts[0]
    assert (
        'api.setColorScale({"autoRange":false,"minimum":2.0,"maximum":85.0,"reverse":true})'
        in scripts[0]
    )


def test_reception_widget_replays_latest_controls_only_for_current_document() -> None:
    scripts: list[str] = []

    class FakePage:
        document_generation = "8"

        def runJavaScript(self, script: str, callback=None) -> None:
            scripts.append(script)
            if callback is not None:
                callback(self.document_generation)

    fake_widget = cast(Any, type("FakeReceptionWidget", (), {})())
    fake_widget.opacity = 0.72
    fake_widget.payload = sample_payload()
    fake_widget._web_engine = True
    fake_widget._page = FakePage()
    fake_widget._render_generation = 9
    fake_widget._loaded_render_generation = -1
    fake_widget._ready = False
    fake_widget._ready_poll_attempts = 4
    fake_widget._set_ready = lambda value: setattr(fake_widget, "_ready", value)
    requested_fits: list[bool] = []
    ready_polls: list[bool] = []
    fake_widget._schedule_viewport_refresh = lambda fit=False: requested_fits.append(fit)
    fake_widget._poll_ready_state = lambda: ready_polls.append(True)
    fake_widget._map_page_generation_loaded = lambda result: (
        ReceptionMapWidget._map_page_generation_loaded(fake_widget, result)
    )

    ReceptionMapWidget.set_opacity(fake_widget, 0.45)
    ReceptionMapWidget.set_opacity(fake_widget, 0.35)
    ReceptionMapWidget.set_color_scale(fake_widget, False, 10.0, 90.0, False)
    ReceptionMapWidget.set_color_scale(fake_widget, False, 12.0, 64.0, True)

    assert scripts == []
    assert fake_widget.payload["opacity"] == 0.35
    assert fake_widget.payload["range_min"] == 12.0
    assert fake_widget.payload["range_max"] == 64.0
    assert fake_widget.payload["reverse"] is True

    ReceptionMapWidget._map_page_loaded(fake_widget, True)

    assert len(scripts) == 1
    assert fake_widget._loaded_render_generation == -1
    assert requested_fits == []
    assert ready_polls == []

    fake_widget._page.document_generation = "9"
    ReceptionMapWidget._map_page_loaded(fake_widget, True)

    assert fake_widget._loaded_render_generation == 9
    assert requested_fits == [True]
    assert ready_polls == [True]
    assert fake_widget._ready_poll_attempts == 0
    assert len(scripts) == 6
    assert "String(window.sloppyReceptionRenderGeneration" in scripts[1]
    assert 'api.setBasemap("osm")' in scripts[2]
    assert "api.setImageryOpacity(1.0)" in scripts[3]
    assert "api.setOpacity(0.35)" in scripts[4]
    assert (
        'api.setColorScale({"autoRange":false,"minimum":12.0,"maximum":64.0,"reverse":true})'
        in scripts[5]
    )
