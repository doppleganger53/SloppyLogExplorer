"""Live Qt WebEngine validation for the multi-log reception map renderer."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sloppy_log_explorer.reception_map_renderer import build_reception_map_html


FOCUS_LATITUDE = 39.774389
FOCUS_LONGITUDE = -75.204944
MERCATOR_EDGE_LATITUDE = 85.05
MERCATOR_EDGE_LONGITUDE = 10.0
MAX_VIEWPORT_RADIUS_METERS = 2_000.0


def synthetic_payload() -> dict[str, object]:
    """Build a deterministic 5 m-cell payload that needs no telemetry files."""
    center_latitude = FOCUS_LATITUDE
    center_longitude = FOCUS_LONGITUDE
    cells: list[dict[str, object]] = []
    values = [18.0, 31.0, 44.0, 57.0, 69.0, 78.0, 86.0, 93.0, 99.0]
    value_index = 0
    for north_offset in (-0.00018, 0.0, 0.00018):
        for east_offset in (-0.00024, 0.0, 0.00024):
            cells.append(
                {
                    "latitude": center_latitude + north_offset,
                    "longitude": center_longitude + east_offset,
                    "cell_size_m": 5,
                    "value": values[value_index],
                    "sample_count": 8 + value_index * 3,
                    "flight_count": 1 + value_index % 4,
                }
            )
            value_index += 1
    # A sparse cell near the edge of the focus radius forces the camera's hard
    # zoom floor to win over ordinary bounds fitting.
    cells.append(
        {
            "latitude": center_latitude + 0.016,
            "longitude": center_longitude,
            "cell_size_m": 5,
            "value": 15.0,
            "sample_count": 1,
            "flight_count": 1,
        }
    )
    # Keep another valid but irrelevant observed cell well outside the density
    # focus. It must remain in the source without zooming the map out.
    cells.append(
        {
            "latitude": center_latitude + 0.065,
            "longitude": center_longitude,
            "cell_size_m": 5,
            "value": 12.0,
            "sample_count": 1,
            "flight_count": 1,
        }
    )
    return {
        "status": "ok",
        "site_name": "Synthetic validation site",
        "telemetry_column": "VFR 2.4G(%)",
        "telemetry_label": "VFR 2.4G(%) ÷ clamp(Power 900M(mW), 10, 500)",
        "reference_column": "Power 900M(mW)",
        "normalization_mode": "ratio",
        "reference_auto_range": False,
        "reference_observed_min": 5.0,
        "reference_observed_max": 1_000.0,
        "reference_range_min": 10.0,
        "reference_range_max": 500.0,
        "clamped_reference_sample_count": 2,
        "opacity": 0.72,
        "cell_size_m": 5,
        "auto_range": False,
        # Match the flat payload shape emitted by MainWindow after generation.
        "range_min": 2.0,
        "range_max": 85.0,
        "reverse": False,
        "viewport_focus": {
            "latitude": center_latitude,
            "longitude": center_longitude,
            "radius_m": MAX_VIEWPORT_RADIUS_METERS,
            "cell_count": 10,
            "sample_count": 181,
            "flight_count": 22,
        },
        "cells": cells,
    }


def mercator_edge_payload() -> dict[str, object]:
    """Build a valid focus close enough to the projection edge to constrain MapLibre."""
    return {
        "status": "ok",
        "site_name": "Synthetic Mercator-edge site",
        "telemetry_column": "VFR 2.4G(%)",
        "unit": "%",
        "cell_size_m": 5,
        "auto_range": False,
        "range_min": 2.0,
        "range_max": 85.0,
        "reverse": False,
        "viewport_focus": {
            "latitude": MERCATOR_EDGE_LATITUDE,
            "longitude": MERCATOR_EDGE_LONGITUDE,
            "radius_m": MAX_VIEWPORT_RADIUS_METERS,
            "cell_count": 3,
            "sample_count": 181,
            "flight_count": 6,
        },
        "cells": [
            {
                "latitude": MERCATOR_EDGE_LATITUDE,
                "longitude": MERCATOR_EDGE_LONGITUDE,
                "value": 72.0,
                "sample_count": 100,
                "flight_count": 3,
            },
            {
                "latitude": MERCATOR_EDGE_LATITUDE - 0.0002,
                "longitude": MERCATOR_EDGE_LONGITUDE + 0.001,
                "value": 64.0,
                "sample_count": 80,
                "flight_count": 2,
            },
            {
                # About 1.8 km south: inside the declared focus, but far enough
                # to reproduce MapLibre's near-edge center constraint.
                "latitude": MERCATOR_EDGE_LATITUDE - 0.016,
                "longitude": MERCATOR_EDGE_LONGITUDE,
                "value": 22.0,
                "sample_count": 1,
                "flight_count": 1,
            },
        ],
    }


def _run_event_loop(timeout_ms: int) -> None:
    from PyQt6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()


def _run_js(page: Any, script: str, timeout_ms: int = 1000) -> Any:
    from PyQt6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    result: dict[str, Any] = {}
    page.runJavaScript(script, lambda value: (result.setdefault("value", value), loop.quit()))
    QTimer.singleShot(timeout_ms, loop.quit)
    loop.exec()
    return result.get("value")


def _wait_for_ready_state(page: Any, attempts: int = 80) -> dict[str, Any] | None:
    state: dict[str, Any] | None = None
    for _ in range(attempts):
        candidate = _run_js(
            page,
            "window.sloppyReceptionMap && window.sloppyReceptionMap.getState "
            "? window.sloppyReceptionMap.getState() : null;",
            timeout_ms=750,
        )
        state = candidate if isinstance(candidate, dict) else None
        if (
            state is not None
            and state.get("ready")
            and int(state.get("renderedCellCount") or 0) > 0
        ):
            return state
        if state is not None and state.get("state") == "error":
            return state
        _run_event_loop(150)
    return state


def _image_metrics(image: Any) -> dict[str, int]:
    from PyQt6.QtGui import QColor

    width = image.width()
    height = image.height()
    unique: set[tuple[int, int, int]] = set()
    colored = 0
    samples = 0
    y_step = max(1, height // 80)
    x_step = max(1, width // 120)
    for y in range(0, height, y_step):
        for x in range(0, width, x_step):
            color = QColor(image.pixel(x, y))
            unique.add((color.red() // 16, color.green() // 16, color.blue() // 16))
            samples += 1
            if max(color.red(), color.green(), color.blue()) - min(
                color.red(), color.green(), color.blue()
            ) > 40:
                colored += 1
    return {
        "width": width,
        "height": height,
        "uniqueBuckets": len(unique),
        "coloredSamples": colored,
        "samples": samples,
    }


def _image_difference_metrics(reference: Any, candidate: Any) -> dict[str, int | float]:
    """Measure map-body pixel changes while excluding controls and attribution."""
    from PyQt6.QtGui import QColor

    width = min(reference.width(), candidate.width())
    height = min(reference.height(), candidate.height())
    x_start = max(0, width // 12)
    x_stop = max(x_start + 1, width - width // 12)
    y_start = max(0, height // 8)
    y_stop = max(y_start + 1, height - height // 7)
    x_step = max(1, (x_stop - x_start) // 100)
    y_step = max(1, (y_stop - y_start) // 60)
    changed = 0
    total_delta = 0.0
    samples = 0
    for y in range(y_start, y_stop, y_step):
        for x in range(x_start, x_stop, x_step):
            before = QColor(reference.pixel(x, y))
            after = QColor(candidate.pixel(x, y))
            deltas = (
                abs(before.red() - after.red()),
                abs(before.green() - after.green()),
                abs(before.blue() - after.blue()),
            )
            samples += 1
            total_delta += sum(deltas) / 3.0
            if max(deltas) >= 18:
                changed += 1
    return {
        "samples": samples,
        "changedSamples": changed,
        "changedRatio": changed / samples if samples else 0.0,
        "meanAbsoluteDelta": total_delta / samples if samples else 0.0,
    }


def _assert_imagery_pixels(
    reference: Any,
    candidate: Any,
    label: str,
) -> dict[str, int | float]:
    """Require the imagery layer to visibly change the fixed map viewport."""
    metrics = _image_difference_metrics(reference, candidate)
    if float(metrics["changedRatio"]) < 0.2 or float(metrics["meanAbsoluteDelta"]) < 8.0:
        raise AssertionError(
            f"{label} did not visibly replace the OpenStreetMap pixels: {metrics}"
        )
    return metrics


def _assert_ready_state(
    state: dict[str, Any] | None,
    expected_cells: int,
    *,
    focus_latitude: float = FOCUS_LATITUDE,
    focus_longitude: float = FOCUS_LONGITUDE,
) -> None:
    if state is None or state.get("state") != "ready" or not state.get("ready"):
        raise AssertionError(f"Reception map did not become ready: {state}")
    if int(state.get("cellCount") or 0) != expected_cells:
        raise AssertionError(f"Reception map cell count is wrong: {state}")
    if int(state.get("renderedCellCount") or 0) < 1:
        raise AssertionError(f"Reception map cells are not visible in the viewport: {state}")
    raw_layers = state.get("layers")
    layers: dict[str, Any] = raw_layers if isinstance(raw_layers, dict) else {}
    for item in ("source", "fill", "outline"):
        if not layers.get(item):
            raise AssertionError(f"Reception map is missing {item}: {state}")
    raw_value_range = state.get("range")
    value_range: dict[str, Any] = raw_value_range if isinstance(raw_value_range, dict) else {}
    if value_range.get("auto") is not False:
        raise AssertionError(f"Reception map did not preserve manual range mode: {state}")
    if float(value_range.get("minimum") or 0) != 2.0 or float(value_range.get("maximum") or 0) != 85.0:
        raise AssertionError(f"Reception map manual range is wrong: {state}")
    raw_camera = state.get("camera")
    camera: dict[str, Any] = raw_camera if isinstance(raw_camera, dict) else {}
    if abs(float(camera.get("pitch") or 0)) > 0.1 or abs(float(camera.get("bearing") or 0)) > 0.1:
        raise AssertionError(f"Reception map is not top-down: {state}")
    bounds = state.get("bounds")
    focus_bounds = state.get("focusBounds")
    viewport_bounds = state.get("viewportBounds")
    for label, candidate in (
        ("data", bounds),
        ("focus", focus_bounds),
        ("viewport", viewport_bounds),
    ):
        if (
            not isinstance(candidate, list)
            or len(candidate) != 2
            or not all(isinstance(point, list) and len(point) == 2 for point in candidate)
        ):
            raise AssertionError(f"Reception map did not report valid {label} bounds: {state}")
    center = camera.get("center")
    if not isinstance(center, list) or len(center) != 2:
        raise AssertionError(f"Reception map did not report a valid camera center: {state}")
    raw_focus = state.get("viewportFocus")
    focus: dict[str, Any] = raw_focus if isinstance(raw_focus, dict) else {}
    if abs(float(focus.get("latitude") or 0) - focus_latitude) > 0.00002 or abs(
        float(focus.get("longitude") or 0) - focus_longitude
    ) > 0.00002:
        raise AssertionError(f"Reception map lost its density focus: {state}")
    if abs(float(center[0]) - focus_longitude) > 0.00002 or abs(
        float(center[1]) - focus_latitude
    ) > 0.00002:
        raise AssertionError(f"Reception map camera was not centered on its density focus: {state}")
    declared_radius = float(focus.get("radiusMeters") or 0)
    visible_radius = float(state.get("viewportRadiusMeters") or 0)
    if declared_radius <= 0 or declared_radius > MAX_VIEWPORT_RADIUS_METERS:
        raise AssertionError(f"Reception map declared an invalid focus radius: {state}")
    if visible_radius <= 0 or visible_radius > MAX_VIEWPORT_RADIUS_METERS + 5.0:
        raise AssertionError(f"Reception map viewport exceeds its 2 km radius cap: {state}")


def validate_reception_map_runtime(
    output: Path,
    state_root: Path | None,
    width: int,
    height: int,
) -> dict[str, Any]:
    """Load a synthetic reception map, inspect live state, and save a screenshot."""
    output = output.resolve()
    if state_root is None:
        state_root = Path(tempfile.mkdtemp(prefix="sloppy-reception-map-runtime-"))
    state_root = state_root.resolve()
    state_root.mkdir(parents=True, exist_ok=True)
    os.environ["APPDATA"] = str(state_root / "appdata")

    from PyQt6.QtCore import QEventLoop, QTimer, QUrl
    from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWidgets import QApplication

    payload = synthetic_payload()
    html_path = state_root / "reception-map-runtime.html"
    html_path.write_text(build_reception_map_html(payload), encoding="utf-8")

    existing_app = QApplication.instance()
    app = existing_app or QApplication(sys.argv)
    profile = QWebEngineProfile("SloppyReceptionMapRuntimeValidation", app)
    cache_path = state_root / "web-cache"
    storage_path = state_root / "web-storage"
    cache_path.mkdir(parents=True, exist_ok=True)
    storage_path.mkdir(parents=True, exist_ok=True)
    profile.setCachePath(str(cache_path))
    profile.setPersistentStoragePath(str(storage_path))
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)

    class ValidationPage(QWebEnginePage):
        def __init__(self, *args: Any) -> None:
            super().__init__(*args)
            self.console_messages: list[str] = []

        def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID) -> None:
            self.console_messages.append(f"{sourceID}:{lineNumber}: {message}")

    page = ValidationPage(profile, app)
    settings = page.settings()
    if settings is None:
        raise AssertionError("QWebEnginePage.settings() returned None")
    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
    view = QWebEngineView()
    view.resize(width, height)
    view.setPage(page)
    view.show()

    def load_document(path: Path) -> bool:
        load_loop = QEventLoop()
        loaded: list[bool] = []

        def load_finished(ok: bool) -> None:
            loaded.append(ok)
            load_loop.quit()

        page.loadFinished.connect(load_finished)
        view.setUrl(QUrl.fromLocalFile(str(path)))
        QTimer.singleShot(15000, load_loop.quit)
        load_loop.exec()
        page.loadFinished.disconnect(load_finished)
        return bool(loaded and loaded[-1])

    if not load_document(html_path):
        view.close()
        raise AssertionError("Qt WebEngine did not finish loading the reception map HTML")

    cells = payload.get("cells")
    expected_cells = len(cells) if isinstance(cells, list) else 0
    state = _wait_for_ready_state(page)
    try:
        _assert_ready_state(state, expected_cells)
    except AssertionError as error:
        worker_probe = _run_js(
            page,
            "(() => { try { const request = new XMLHttpRequest(); "
            "const workerUrl = maplibregl.getWorkerUrl ? maplibregl.getWorkerUrl() : maplibregl.workerUrl; "
            "request.open('GET', workerUrl, false); request.send(); "
            "return {url: workerUrl, status: request.status, "
            "prefix: request.responseText.slice(0, 80)}; } "
            "catch (probeError) { return {error: String(probeError)}; } })();",
        )
        view.close()
        raise AssertionError(
            f"{error}; worker={worker_probe}; console={page.console_messages}"
        ) from error

    initial_camera = state.get("camera") if isinstance(state, dict) else None
    opacity_update = _run_js(
        page,
        "(() => { const updated = window.sloppyReceptionMap.setOpacity(0.35); "
        "const current = window.sloppyReceptionMap.getState(); "
        "return {updated, state: current, "
        "fill: window.__sloppyReceptionDebugMap.getPaintProperty('reception-cells-fill', 'fill-opacity'), "
        "outline: window.__sloppyReceptionDebugMap.getPaintProperty('reception-cells-outline', 'line-opacity')}; })();",
    )
    if not isinstance(opacity_update, dict) or opacity_update.get("updated") is not True:
        view.close()
        raise AssertionError(f"Reception map rejected a live opacity update: {opacity_update}")
    opacity_state = opacity_update.get("state")
    if not isinstance(opacity_state, dict) or abs(float(opacity_state.get("opacity") or 0) - 0.35) > 1e-9:
        view.close()
        raise AssertionError(f"Reception map did not retain live opacity: {opacity_update}")
    if abs(float(opacity_update.get("fill") or 0) - 0.35) > 1e-9 or abs(
        float(opacity_update.get("outline") or 0) - 0.4375
    ) > 1e-9:
        view.close()
        raise AssertionError(f"Reception map paint opacity is wrong: {opacity_update}")
    if opacity_state.get("camera") != initial_camera:
        view.close()
        raise AssertionError(f"Live opacity moved the reception camera: {opacity_update}")

    color_scale_update = _run_js(
        page,
        "(() => { const updated = window.sloppyReceptionMap.setColorScale("
        "{autoRange:false, minimum:12, maximum:64, reverse:true}); "
        "const current = window.sloppyReceptionMap.getState(); "
        "return {updated, state: current, "
        "minimum: document.getElementById('legend-min').textContent, "
        "maximum: document.getElementById('legend-max').textContent, "
        "detail: document.getElementById('legend-detail').textContent}; })();",
    )
    if not isinstance(color_scale_update, dict) or color_scale_update.get("updated") is not True:
        view.close()
        raise AssertionError(f"Reception map rejected a live color-scale update: {color_scale_update}")
    color_state = color_scale_update.get("state")
    if not isinstance(color_state, dict):
        view.close()
        raise AssertionError(f"Reception map returned no live color-scale state: {color_scale_update}")
    color_range = color_state.get("range")
    if not isinstance(color_range, dict) or color_range != {
        "minimum": 12,
        "maximum": 64,
        "auto": False,
        "reverse": True,
    }:
        view.close()
        raise AssertionError(f"Reception map retained the wrong color scale: {color_scale_update}")
    if color_scale_update.get("minimum") != "12" or color_scale_update.get("maximum") != "64":
        view.close()
        raise AssertionError(f"Reception map legend ignored the live color scale: {color_scale_update}")
    if color_state.get("camera") != initial_camera:
        view.close()
        raise AssertionError(f"Live color-scale update moved the reception camera: {color_scale_update}")
    _run_js(
        page,
        "window.sloppyReceptionMap.setColorScale("
        "{autoRange:false, minimum:2, maximum:85, reverse:false});",
    )

    _run_event_loop(750)
    osm_image = view.grab().toImage().copy()

    _run_js(
        page,
        "window.sloppyReceptionMap.setBasemap('imagery');"
        "window.sloppyReceptionMap.setImageryOpacity(0.8); true;",
    )
    _run_event_loop(2000)
    imagery_state = _run_js(page, "window.sloppyReceptionMap.getState();")
    _assert_ready_state(imagery_state, expected_cells)
    imagery_basemap = imagery_state.get("basemap") if isinstance(imagery_state, dict) else None
    if not isinstance(imagery_basemap, dict):
        view.close()
        raise AssertionError(f"Reception map did not report imagery state: {imagery_state}")
    if imagery_basemap.get("selected") != "imagery" or abs(
        float(imagery_basemap.get("imageryOpacity") or 0) - 0.8
    ) > 1e-9:
        view.close()
        raise AssertionError(f"Reception imagery controls were not applied: {imagery_state}")
    if imagery_basemap.get("provider") not in {"naip", "gibs"}:
        view.close()
        raise AssertionError(f"Reception imagery did not retain a usable provider: {imagery_state}")
    if imagery_state.get("camera") != initial_camera:
        view.close()
        raise AssertionError(f"Reception imagery switch moved the camera: {imagery_state}")
    if abs(float(imagery_state.get("opacity") or 0) - 0.35) > 1e-9:
        view.close()
        raise AssertionError(f"Imagery opacity changed HeatMap opacity: {imagery_state}")
    imagery_image = view.grab().toImage().copy()
    try:
        imagery_difference = _assert_imagery_pixels(
            osm_image,
            imagery_image,
            "HeatMap NAIP imagery",
        )
    except AssertionError:
        view.close()
        raise

    naip_fallback_state = _run_js(
        page,
        "handleBasemapError({sourceId:'usgs-naip-raster-source'});"
        "window.sloppyReceptionMap.getState();",
    )
    naip_fallback_basemap = (
        naip_fallback_state.get("basemap") if isinstance(naip_fallback_state, dict) else None
    )
    if not isinstance(naip_fallback_basemap, dict) or naip_fallback_basemap.get("provider") != "gibs":
        view.close()
        raise AssertionError(
            f"Reception map did not fall back from NAIP to GIBS: {naip_fallback_state}"
        )
    _run_event_loop(750)
    gibs_image = view.grab().toImage().copy()
    try:
        gibs_difference = _assert_imagery_pixels(
            osm_image,
            gibs_image,
            "HeatMap NASA GIBS fallback",
        )
        naip_contribution = _assert_imagery_pixels(
            gibs_image,
            imagery_image,
            "HeatMap NAIP contribution over NASA GIBS",
        )
    except AssertionError:
        view.close()
        raise

    osm_fallback_state = _run_js(
        page,
        "handleBasemapError({sourceId:'nasa-gibs-raster-source'});"
        "window.sloppyReceptionMap.getState();",
    )
    osm_fallback_basemap = (
        osm_fallback_state.get("basemap") if isinstance(osm_fallback_state, dict) else None
    )
    if not isinstance(osm_fallback_basemap, dict) or osm_fallback_basemap.get("provider") != "osmFallback":
        view.close()
        raise AssertionError(
            f"Reception map did not fall back from GIBS to OSM: {osm_fallback_state}"
        )

    _run_js(page, "window.sloppyReceptionMap.setBasemap('imagery'); true;")
    _run_event_loop(2000)
    imagery_retry_state = _run_js(page, "window.sloppyReceptionMap.getState();")
    retry_basemap = (
        imagery_retry_state.get("basemap") if isinstance(imagery_retry_state, dict) else None
    )
    if not isinstance(retry_basemap, dict) or retry_basemap.get("provider") not in {"naip", "gibs"}:
        view.close()
        raise AssertionError(
            f"Reception imagery providers did not recover on retry: {imagery_retry_state}"
        )

    view.resize(max(width + 600, int(width * 1.5)), max(320, height // 2))
    _run_event_loop(250)
    _run_js(page, "window.sloppyReceptionMap.refresh({fit:true}); true;")
    _run_event_loop(500)
    wide_fit = _wait_for_ready_state(page)
    try:
        _assert_ready_state(wide_fit, expected_cells)
    except AssertionError:
        view.close()
        raise

    view.resize(width, height)
    _run_event_loop(250)
    _run_js(page, "window.sloppyReceptionMap.refresh({fit:true}); true;")
    _run_event_loop(500)

    legend = _run_js(
        page,
        "({title: document.getElementById('legend-title').textContent, "
        "minimum: document.getElementById('legend-min').textContent, "
        "maximum: document.getElementById('legend-max').textContent, "
        "detail: document.getElementById('legend-detail').textContent});",
    )
    expected_title = "VFR 2.4G(%) ÷ clamp(Power 900M(mW), 10, 500)"
    if not isinstance(legend, dict) or legend.get("title") != expected_title:
        view.close()
        raise AssertionError(f"Reception map legend is missing telemetry context: {legend}")
    if "5 m cells" not in str(legend.get("detail") or ""):
        view.close()
        raise AssertionError(f"Reception map legend is missing cell resolution: {legend}")
    if legend.get("minimum") != "2" or legend.get("maximum") != "85":
        view.close()
        raise AssertionError(f"Reception map legend has the wrong manual bounds: {legend}")
    if "Manual range" not in str(legend.get("detail") or ""):
        view.close()
        raise AssertionError(f"Reception map legend lost manual range mode: {legend}")

    _run_js(page, "window.sloppyReceptionMap.refresh({fit:true}); true;")
    _run_event_loop(1000)
    after_fit = _wait_for_ready_state(page)
    try:
        _assert_ready_state(after_fit, expected_cells)
    except AssertionError:
        view.close()
        raise

    output.parent.mkdir(parents=True, exist_ok=True)
    image = view.grab().toImage()
    if not image.save(str(output)):
        view.close()
        raise AssertionError(f"Failed to save reception map screenshot to {output}")
    metrics = _image_metrics(image)
    if metrics["uniqueBuckets"] < 6 or metrics["coloredSamples"] < 1:
        view.close()
        raise AssertionError(f"Reception map screenshot looked blank or too flat: {metrics}")

    edge_payload = mercator_edge_payload()
    edge_html_path = state_root / "reception-map-runtime-mercator-edge.html"
    edge_html_path.write_text(build_reception_map_html(edge_payload), encoding="utf-8")
    if not load_document(edge_html_path):
        view.close()
        raise AssertionError("Qt WebEngine did not load the Mercator-edge reception map")
    edge_cells = edge_payload.get("cells")
    edge_expected_cells = len(edge_cells) if isinstance(edge_cells, list) else 0
    edge_state = _wait_for_ready_state(page)
    _assert_ready_state(
        edge_state,
        edge_expected_cells,
        focus_latitude=MERCATOR_EDGE_LATITUDE,
        focus_longitude=MERCATOR_EDGE_LONGITUDE,
    )

    view.close()
    page.deleteLater()
    profile.deleteLater()
    if existing_app is None:
        app.quit()
    return {
        "html": str(html_path),
        "screenshot": str(output),
        "loaded": True,
        "initial": state,
        "wideFit": wide_fit,
        "afterFit": after_fit,
        "opacityUpdate": opacity_update,
        "colorScaleUpdate": color_scale_update,
        "imagery": imagery_state,
        "imageryDifference": imagery_difference,
        "naipContribution": naip_contribution,
        "naipFallback": naip_fallback_state,
        "gibsDifference": gibs_difference,
        "osmFallback": osm_fallback_state,
        "imageryRetry": imagery_retry_state,
        "mercatorEdge": edge_state,
        "legend": legend,
        "image": metrics,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate the multi-log reception map in a live Qt WebEngine view."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("validation_artifacts/reception-map-webengine.png"),
    )
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=820)
    args = parser.parse_args()

    result = validate_reception_map_runtime(
        output=args.output,
        state_root=args.state_root,
        width=args.width,
        height=args.height,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
