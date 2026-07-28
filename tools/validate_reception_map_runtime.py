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


def synthetic_payload() -> dict[str, object]:
    """Build a deterministic 5 m-cell payload that needs no telemetry files."""
    center_latitude = 39.774389
    center_longitude = -75.204944
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
    return {
        "status": "ok",
        "site_name": "Synthetic validation site",
        "telemetry_column": "VFR 2.4G(%)",
        "unit": "%",
        "cell_size_m": 5,
        "auto_range": False,
        "range": {"min": 0.0, "max": 100.0},
        "reverse": False,
        "cells": cells,
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


def _assert_ready_state(state: dict[str, Any] | None, expected_cells: int) -> None:
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
    if float(value_range.get("minimum") or 0) != 0.0 or float(value_range.get("maximum") or 0) != 100.0:
        raise AssertionError(f"Reception map manual range is wrong: {state}")
    raw_camera = state.get("camera")
    camera: dict[str, Any] = raw_camera if isinstance(raw_camera, dict) else {}
    if abs(float(camera.get("pitch") or 0)) > 0.1 or abs(float(camera.get("bearing") or 0)) > 0.1:
        raise AssertionError(f"Reception map is not top-down: {state}")
    bounds = state.get("bounds")
    if not isinstance(bounds, list) or len(bounds) != 2:
        raise AssertionError(f"Reception map did not report fitted data bounds: {state}")
    center = camera.get("center")
    if (
        not isinstance(center, list)
        or len(center) != 2
        or not all(isinstance(point, list) and len(point) == 2 for point in bounds)
    ):
        raise AssertionError(f"Reception map did not report a valid camera center: {state}")
    expected_longitude = (float(bounds[0][0]) + float(bounds[1][0])) / 2.0
    expected_latitude = (float(bounds[0][1]) + float(bounds[1][1])) / 2.0
    if abs(float(center[0]) - expected_longitude) > 0.00002 or abs(float(center[1]) - expected_latitude) > 0.00002:
        raise AssertionError(f"Reception map camera was not fitted to its observed cells: {state}")


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

    load_loop = QEventLoop()
    loaded: dict[str, bool] = {}
    page.loadFinished.connect(lambda ok: (loaded.setdefault("ok", ok), load_loop.quit()))
    view.setUrl(QUrl.fromLocalFile(str(html_path)))
    QTimer.singleShot(15000, load_loop.quit)
    load_loop.exec()
    if not loaded.get("ok"):
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

    legend = _run_js(
        page,
        "({title: document.getElementById('legend-title').textContent, "
        "detail: document.getElementById('legend-detail').textContent});",
    )
    if not isinstance(legend, dict) or legend.get("title") != "VFR 2.4G(%)":
        view.close()
        raise AssertionError(f"Reception map legend is missing telemetry context: {legend}")
    if "5 m cells" not in str(legend.get("detail") or ""):
        view.close()
        raise AssertionError(f"Reception map legend is missing cell resolution: {legend}")

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
        "afterFit": after_fit,
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
