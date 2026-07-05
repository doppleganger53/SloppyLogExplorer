"""Live Qt WebEngine validation for the GPS map renderer."""

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

from sloppy_log_explorer.models import GpsGradientOptions
from sloppy_log_explorer.parser import load_log, relative_seconds
from sloppy_log_explorer.plotting import build_gps_map_html


def _run_event_loop(timeout_ms: int) -> None:
    from PyQt6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    # Let the WebEngine event queue drain for a short, deterministic window.
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


def _wait_for_ready_state(page: Any, attempts: int = 60) -> dict[str, Any] | None:
    state = None
    for _ in range(attempts):
        # Poll instead of sleeping blindly because MapLibre becomes ready
        # asynchronously after the HTML page and tile assets finish loading.
        state = _run_js(
            page,
            "window.sloppyGpsMap && window.sloppyGpsMap.getState ? window.sloppyGpsMap.getState() : null;",
            timeout_ms=500,
        )
        if state and state.get("state") == "ready" and (state.get("native3dPathReady") or state.get("webglPathReady")):
            return state
        _run_event_loop(250)
    return state


def _assert_3d_state(state: dict[str, Any] | None, label: str, min_pitch: float | None = None) -> None:
    if not state or state.get("state") != "ready":
        raise AssertionError(f"GPS map did not stay ready at {label}: {state}")
    raw_layers = state.get("layers")
    raw_camera = state.get("camera")
    layers: dict[str, Any] = raw_layers if isinstance(raw_layers, dict) else {}
    camera: dict[str, Any] = raw_camera if isinstance(raw_camera, dict) else {}
    if not layers.get("flight-extrusions"):
        raise AssertionError(f"GPS map is missing the native 3D extrusion path layer at {label}: {state}")
    if not state.get("native3dPathReady"):
        raise AssertionError(f"GPS map native 3D extrusion path is not ready at {label}: {state}")
    # The WebGL diagnostic layer is optional, but if it exists it should carry
    # real vertices so it can prove the renderer path is working.
    if layers.get("flight-elevation-layer") and int(state.get("ribbonVertexCount") or 0) <= 0:
        raise AssertionError(f"GPS map WebGL diagnostic ribbon has no vertices at {label}: {state}")
    if float(camera.get("maxPitch") or 0) < 85:
        raise AssertionError(f"GPS map camera cannot pitch to ground level at {label}: {state}")
    if float(camera.get("maxZoom") or 0) < 19:
        raise AssertionError(f"GPS map camera cannot zoom close enough at {label}: {state}")
    if min_pitch is not None and float(camera.get("pitch") or 0) < min_pitch:
        raise AssertionError(f"GPS map camera pitch is too shallow at {label}: {state}")


def _image_metrics(image: Any) -> dict[str, int]:
    from PyQt6.QtGui import QColor

    width = image.width()
    height = image.height()
    unique: set[tuple[int, int, int]] = set()
    bright = 0
    colored = 0
    samples = 0
    y_step = max(1, height // 80)
    x_step = max(1, width // 120)
    for y in range(0, height, y_step):
        for x in range(0, width, x_step):
            color = QColor(image.pixel(x, y))
            unique.add((color.red() // 16, color.green() // 16, color.blue() // 16))
            samples += 1
            if color.red() > 200 or color.green() > 200 or color.blue() > 200:
                bright += 1
            if max(color.red(), color.green(), color.blue()) - min(color.red(), color.green(), color.blue()) > 40:
                colored += 1
    return {
        "width": width,
        "height": height,
        "uniqueBuckets": len(unique),
        "brightSamples": bright,
        "coloredSamples": colored,
        "samples": samples,
    }


def _gps_runtime_options(
    color_column: str | None,
    scope_start_seconds: float,
    scope_end_seconds: float,
) -> GpsGradientOptions:
    return GpsGradientOptions(
        color_column=color_column,
        scope_start_seconds=scope_start_seconds,
        scope_end_seconds=scope_end_seconds,
    )


def validate_gps_map_runtime(
    log_path: Path,
    color_column: str | None,
    output: Path,
    state_root: Path | None,
    width: int,
    height: int,
) -> dict[str, Any]:
    log_path = log_path.resolve()
    output = output.resolve()
    if state_root is None:
        state_root = Path(tempfile.mkdtemp(prefix="sloppy-gps-map-runtime-"))
    state_root = state_root.resolve()
    state_root.mkdir(parents=True, exist_ok=True)
    os.environ["APPDATA"] = str(state_root / "appdata")

    from PyQt6.QtCore import QEventLoop, QTimer, QUrl
    from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWidgets import QApplication

    log = load_log(log_path)
    if not log.info.has_gps:
        raise AssertionError(f"{log_path} does not contain detected GPS coordinates")

    elapsed_values = relative_seconds(log)
    duration = max(elapsed_values) if elapsed_values else 0.0
    scope_start = 1.0 if duration > 3.0 else 0.0
    scope_end = min(duration, scope_start + 3.0) if duration > scope_start else duration
    if scope_end <= scope_start:
        scope_start = 0.0
        scope_end = duration
    cursor_elapsed = scope_start + max(0.0, min(1.0, scope_end - scope_start))

    options = _gps_runtime_options(
        color_column=color_column,
        scope_start_seconds=scope_start,
        scope_end_seconds=scope_end,
    )
    html_path = state_root / "gps-map-runtime.html"
    # Write the HTML to disk so the page exercises the same file:// loading
    # path that the desktop widget uses.
    html_path.write_text(build_gps_map_html(log, options), encoding="utf-8")

    app = QApplication.instance() or QApplication(sys.argv)
    profile = QWebEngineProfile("SloppyLogExplorerRuntimeValidation", app)
    cache_path = state_root / "web-cache"
    storage_path = state_root / "web-storage"
    cache_path.mkdir(parents=True, exist_ok=True)
    storage_path.mkdir(parents=True, exist_ok=True)
    profile.setCachePath(str(cache_path))
    profile.setPersistentStoragePath(str(storage_path))
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)

    page = QWebEnginePage(profile, app)
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
        raise AssertionError("Qt WebEngine did not finish loading the GPS map HTML")

    initial_state = _wait_for_ready_state(page)
    try:
        _assert_3d_state(initial_state, "initial")
    except AssertionError:
        view.close()
        raise

    cursor_payload = {
        "index": 3,
        "row": 4,
        "elapsedSeconds": cursor_elapsed,
        "durationSeconds": max(0.0, scope_end - scope_start),
        "scopeStartSeconds": scope_start,
        "scopeEndSeconds": scope_end,
        "playing": False,
        "speed": 2,
        "values": [{"label": "Current(A)", "value": 30}, {"label": "Alt(m)", "value": 16}],
    }
    _run_js(page, f"window.sloppyGpsMap.setCursor({json.dumps(cursor_payload)}); true;")
    cursor_state = _wait_for_ready_state(page)
    if cursor_state is None:
        view.close()
        raise AssertionError("GPS map cursor state was not reported")
    cursor = cursor_state.get("cursor")
    if not isinstance(cursor, dict):
        view.close()
        raise AssertionError(f"GPS map cursor state was not reported: {cursor_state}")
    if int(cursor.get("row") or 0) != 4 or abs(float(cursor.get("elapsedSeconds") or 0) - cursor_elapsed) > 0.1:
        view.close()
        raise AssertionError(f"GPS map cursor did not update to the requested point: {cursor_state}")
    if abs(float(cursor.get("scopeStartSeconds") or 0) - scope_start) > 0.1:
        view.close()
        raise AssertionError(f"GPS map cursor did not report scoped start: {cursor_state}")
    if abs(float(cursor.get("scopeEndSeconds") or 0) - scope_end) > 0.1:
        view.close()
        raise AssertionError(f"GPS map cursor did not report scoped end: {cursor_state}")
    raw_playback = cursor_state.get("playback")
    playback: dict[str, Any] = raw_playback if isinstance(raw_playback, dict) else {}
    if float(playback.get("speed") or 0) != 2.0:
        view.close()
        raise AssertionError(f"GPS map playback state did not preserve requested speed: {cursor_state}")
    if abs(float(playback.get("scopeStartSeconds") or 0) - scope_start) > 0.1:
        view.close()
        raise AssertionError(f"GPS map playback state did not report scoped start: {cursor_state}")
    if abs(float(playback.get("scopeEndSeconds") or 0) - scope_end) > 0.1:
        view.close()
        raise AssertionError(f"GPS map playback state did not report scoped end: {cursor_state}")

    _run_js(
        page,
        (
            "window.sloppyGpsMap.setCameraMode('ground', {animate:false, fit:false});"
            "window.sloppyGpsMap.refresh({fit:false});"
            "true;"
        ),
    )
    after_camera_state = _wait_for_ready_state(page)
    _run_js(page, "window.sloppyGpsMap.setCameraMode('orbit', {animate:false, fit:true}); true;")
    after_fit_state = _wait_for_ready_state(page)
    try:
        _assert_3d_state(after_camera_state, "ground camera", min_pitch=80)
        _assert_3d_state(after_fit_state, "fit/orbit camera", min_pitch=50)
    except AssertionError:
        view.close()
        raise

    _run_event_loop(1200)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Sample the viewport after the map has settled so the screenshot reflects
    # both the rendered terrain and the flight overlay.
    image = view.grab().toImage()
    if not image.save(str(output)):
        view.close()
        raise AssertionError(f"failed to save map screenshot to {output}")
    metrics = _image_metrics(image)
    if metrics["uniqueBuckets"] < 8 or metrics["brightSamples"] < 1 or metrics["coloredSamples"] < 1:
        view.close()
        raise AssertionError(f"GPS map screenshot looked blank or too flat: {metrics}")

    view.close()
    app.quit()
    return {
        "log": str(log_path),
        "html": str(html_path),
        "screenshot": str(output),
        "loaded": True,
        "initial": initial_state,
        "afterCursor": cursor_state,
        "afterCamera": after_camera_state,
        "afterFit": after_fit_state,
        "image": metrics,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the MapLibre 3D GPS map in a live Qt WebEngine view.")
    parser.add_argument("log", type=Path)
    parser.add_argument("--color-column")
    parser.add_argument("--output", type=Path, default=Path("validation_artifacts/flight-map-3d-webengine.png"))
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=820)
    args = parser.parse_args()

    result = validate_gps_map_runtime(
        log_path=args.log,
        color_column=args.color_column,
        output=args.output,
        state_root=args.state_root,
        width=args.width,
        height=args.height,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
