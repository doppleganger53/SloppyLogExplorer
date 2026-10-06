"""Live Qt WebEngine validation for the GPS map renderer."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
import time
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
        if state and state.get("state") == "ready" and state.get("webglPathReady"):
            return state
        _run_event_loop(250)
    return state


def _wait_for_basemap_tiles(page: Any, map_name: str, timeout_seconds: float = 60.0) -> None:
    """Wait for actual source completion before comparing provider pixels."""
    deadline = time.monotonic() + timeout_seconds
    settled_polls = 0
    while time.monotonic() < deadline:
        # Allow visibility changes to schedule their tile requests before
        # accepting loaded state. Cold WMS requests can exceed two seconds.
        _run_event_loop(200)
        loaded = _run_js(
            page,
            f"Boolean(window.{map_name} && !window.{map_name}.isMoving() "
            f"&& window.{map_name}.areTilesLoaded());",
        )
        settled_polls = settled_polls + 1 if loaded is True else 0
        if settled_polls >= 2:
            return
    raise AssertionError(f"Map tile loading did not settle within {timeout_seconds:g} seconds")


def _camera_matches(before: Any, after: Any) -> bool:
    """Ignore projection roundoff while checking complete camera preservation."""
    if isinstance(before, (int, float)) and isinstance(after, (int, float)):
        return math.isclose(before, after, rel_tol=0.0, abs_tol=1e-7)
    if isinstance(before, dict) and isinstance(after, dict):
        return before.keys() == after.keys() and all(_camera_matches(before[key], after[key]) for key in before)
    if isinstance(before, list) and isinstance(after, list):
        return len(before) == len(after) and all(_camera_matches(left, right) for left, right in zip(before, after))
    return before == after


def _validate_vendor_attribution_security(page: Any, map_name: str) -> dict[str, Any]:
    result = _run_js(page, """(() => {
      const control = new maplibregl.AttributionControl({customAttribution:
        '<a href="#test" onclick="window.injected=true" onmouseover="window.injected=true">Test</a>'});
      const container = control.onAdd(window.""" + map_name + """);
      const unsafeAttributes = container.querySelectorAll('[onclick], [onmouseover]').length;
      const version = maplibregl.getVersion();
      control.onRemove();
      return {version, unsafeAttributes};
    })();""")
    if not isinstance(result, dict) or result.get("version") != "6.4.1" or result.get("unsafeAttributes") != 0:
        raise AssertionError(f"Bundled MapLibre attribution sanitization failed: {result}")
    return result


def _assert_gps_overlay_layout(page: Any) -> dict[str, Any]:
    result = _run_js(page, """(() => {
      const boxes = ['legend','playbackOverlay','mapAttribution','cameraControls','measurementControls'].map(id => {
        const element = document.getElementById(id);
        if (!element || element.hidden) return null;
        const r = element.getBoundingClientRect();
        return {id,left:r.left,right:r.right,top:r.top,bottom:r.bottom};
      }).filter(Boolean);
      return {width:innerWidth,height:innerHeight,boxes};
    })();""")
    if not isinstance(result, dict):
        raise AssertionError("GPS map overlay geometry was not available")
    boxes = result["boxes"]
    for index, box in enumerate(boxes):
        if box["left"] < 0 or box["right"] > result["width"] or box["top"] < 0 or box["bottom"] > result["height"]:
            raise AssertionError(f"GPS overlay extends outside the viewport: {result}")
        for other in boxes[index + 1:]:
            if box["left"] < other["right"] and box["right"] > other["left"] and box["top"] < other["bottom"] and box["bottom"] > other["top"]:
                raise AssertionError(f"GPS overlays overlap: {result}")
    return result


def _validate_ground_measurement(page: Any) -> dict[str, Any]:
    result = _run_js(page, """(() => {
      const api = window.sloppyGpsMap, map = window.__sloppyDebugMap;
      const before = api.getState(), zoomEnabled = map.doubleClickZoom.isEnabled();
      const center = map.getCenter();
      api.setMeasurementActive(true);
      [[center.lng,center.lat],[center.lng+0.001,center.lat],
       [center.lng+0.001,center.lat+0.001]].forEach(point => {
        map.fire('click',{lngLat:new maplibregl.LngLat(...point),originalEvent:{detail:1}});
      });
      const added = api.getState().measurement;
      api.moveMeasurementPoint(1,[center.lng+0.0005,center.lat]);
      const moved = api.getState().measurement;
      api.removeMeasurementPoint(2);
      const removed = api.getState().measurement;
      api.setMeasurementActive(false);
      const finished = api.getState().measurement;
      api.clearMeasurement();
      const after = api.getState();
      return {before,added,moved,removed,finished,after,
        zoomRestored:zoomEnabled === map.doubleClickZoom.isEnabled()};
    })();""")
    if not isinstance(result, dict):
        raise AssertionError("Ground measurement did not report state")
    if len(result["added"]["points"]) != 3 or result["added"]["meters"] <= 0:
        raise AssertionError(f"Measurement map clicks failed: {result}")
    if result["moved"]["points"][1] == result["added"]["points"][1]:
        raise AssertionError(f"Measurement point did not move: {result}")
    if len(result["removed"]["points"]) != 2 or result["removed"]["meters"] >= result["added"]["meters"]:
        raise AssertionError(f"Measurement removal did not update the total: {result}")
    if result["finished"]["active"] or not result["zoomRestored"]:
        raise AssertionError(f"Measurement did not restore normal map interaction: {result}")
    if result["after"]["measurement"] != {"active": False, "points": [], "meters": 0}:
        raise AssertionError(f"Measurement did not clear: {result}")
    for key in ("camera", "cursor", "playback"):
        if not _camera_matches(result["before"][key], result["after"][key]):
            raise AssertionError(f"Measurement changed flight {key}: {result}")
    return {key: result[key] for key in ("added", "moved", "removed", "finished", "zoomRestored")}


def _validate_imagery_zoom(page: Any, view: Any, output: Path) -> list[dict[str, Any]]:
    output.parent.mkdir(parents=True, exist_ok=True)
    original = _run_js(page, "window.sloppyGpsMap.getState();")
    results = []
    _run_js(page, "window.sloppyGpsMap.setBasemap('imagery');"
            "window.sloppyGpsMap.setImageryOpacity(1); window.__sloppyDebugMap.setMaxZoom(22); true;")
    try:
        for zoom in (16, 17, 18, 19, 21):
            _run_js(page, "performance.clearResourceTimings();"
                    f"window.__sloppyDebugMap.jumpTo({{zoom:{zoom},pitch:0,bearing:0}}); true;")
            _wait_for_basemap_tiles(page, "__sloppyDebugMap")
            state = _run_js(page, "window.sloppyGpsMap.getState();")
            if not isinstance(state, dict) or state["basemap"]["provider"] != "naip":
                raise AssertionError(f"Imagery provider disappeared at zoom {zoom}: {state}")
            urls = _run_js(page, "performance.getEntriesByType('resource')"
                           ".filter(e=>e.name.includes('ImageServer/exportImage')).map(e=>e.name);")
            from urllib.parse import parse_qs, urlsplit
            tile_zooms = []
            for url in urls or []:
                bbox = [float(value) for value in parse_qs(urlsplit(url).query)["bbox"][0].split(",")]
                tile_zooms.append(round(math.log2(40075016.68557849 / (bbox[2] - bbox[0]))))
            if any(level > 19 for level in tile_zooms):
                raise AssertionError(f"Imagery requested unavailable detail above zoom 19: {tile_zooms}")
            # MapLibre's 512-pixel camera world requests 256-pixel raster tiles
            # one canonical level above camera zoom, capped at source maxzoom.
            expected_level = min(zoom + 1, 19)
            if tile_zooms and max(tile_zooms) != expected_level:
                raise AssertionError(f"Imagery did not request the expected detail at zoom {zoom}: {tile_zooms}")
            if expected_level not in tile_zooms:
                # Already cached tiles need no network request; the source must
                # still be complete at the new viewport.
                loaded = _run_js(page, "window.__sloppyDebugMap.isSourceLoaded('usgs-naip-raster-source');")
                if loaded is not True:
                    raise AssertionError(f"Imagery did not complete the new zoom {zoom}")
            screenshot = output.with_stem(output.stem + f"-zoom-{zoom}")
            if not view.grab().save(str(screenshot)):
                raise AssertionError(f"Could not save imagery zoom screenshot: {screenshot}")
            results.append({"zoom": zoom, "tileZooms": sorted(set(tile_zooms)), "basemap": state["basemap"],
                            "screenshot": str(screenshot)})
    finally:
        _run_js(page, f"window.__sloppyDebugMap.setMaxZoom({original['camera']['maxZoom']});"
                f"window.__sloppyDebugMap.jumpTo({json.dumps(original['camera'])});"
                f"window.sloppyGpsMap.setImageryOpacity({original['basemap']['imageryOpacity']}); true;")
    return results


def _assert_3d_state(state: dict[str, Any] | None, label: str, min_pitch: float | None = None) -> None:
    if not state or state.get("state") != "ready":
        raise AssertionError(f"GPS map did not stay ready at {label}: {state}")
    raw_layers = state.get("layers")
    raw_camera = state.get("camera")
    layers: dict[str, Any] = raw_layers if isinstance(raw_layers, dict) else {}
    camera: dict[str, Any] = raw_camera if isinstance(raw_camera, dict) else {}
    if layers.get("flight-extrusions"):
        raise AssertionError(f"GPS map must not extrude walls beneath the flight path at {label}: {state}")
    if not layers.get("flight-elevation-layer") or not state.get("webglPathReady"):
        raise AssertionError(f"GPS map elevated ribbon is not ready at {label}: {state}")
    if int(state.get("ribbonVertexCount") or 0) <= 0 or state.get("elevationLayerError"):
        raise AssertionError(f"GPS map elevated ribbon failed at {label}: {state}")
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


def _validate_outside_naip_fallback(
    page: Any,
    view: Any,
    api_name: str,
    map_name: str,
    output: Path,
) -> dict[str, Any]:
    """Exercise GIBS outside NAIP coverage without simulating provider errors."""
    original_camera = _run_js(
        page,
        f"({{center:window.{map_name}.getCenter().toArray(),"
        f"zoom:window.{map_name}.getZoom(),pitch:window.{map_name}.getPitch(),"
        f"bearing:window.{map_name}.getBearing()}});",
    )
    _run_js(
        page,
        f"window.{api_name}.setBasemap('osm');"
        f"window.{map_name}.jumpTo({{center:[10,51],zoom:6,pitch:0,bearing:0}}); true;",
    )
    _wait_for_basemap_tiles(page, map_name)
    osm_image = view.grab().toImage().copy()
    _run_js(page, f"window.{api_name}.setBasemap('imagery'); true;")
    _wait_for_basemap_tiles(page, map_name)
    state = _run_js(page, f"window.{api_name}.getState();")
    if not isinstance(state, dict) or state.get("basemap", {}).get("provider") != "gibs":
        raise AssertionError(f"Non-NAIP viewport did not use NASA GIBS: {state}")
    gibs_image = view.grab().toImage().copy()
    difference = _assert_imagery_pixels(osm_image, gibs_image, "NASA GIBS outside NAIP coverage")
    output.parent.mkdir(parents=True, exist_ok=True)
    if not gibs_image.save(str(output)):
        raise AssertionError(f"Failed to save non-NAIP screenshot: {output}")
    _run_js(page, f"window.{map_name}.jumpTo({json.dumps(original_camera)}); true;")
    _wait_for_basemap_tiles(page, map_name)
    return {"basemap": state["basemap"], "camera": state["camera"], "difference": difference,
            "screenshot": str(output)}


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


def _coerce_finite_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _valid_gps_elapsed_values(log: Any, elapsed_values: list[float]) -> list[float]:
    gps = log.gps_columns
    if gps is None:
        return []

    df = log.dataframe
    valid: list[float] = []
    for row_index in range(min(len(df), len(elapsed_values))):
        lat = _coerce_finite_float(df[gps.latitude].iloc[row_index])
        lon = _coerce_finite_float(df[gps.longitude].iloc[row_index])
        if lat is None or lon is None:
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        if lat == 0.0 and lon == 0.0:
            continue
        elapsed = _coerce_finite_float(elapsed_values[row_index])
        if elapsed is not None:
            valid.append(elapsed)
    return valid


def _fallback_scope(elapsed_values: list[float]) -> tuple[float, float]:
    duration = max(elapsed_values) if elapsed_values else 0.0
    scope_start = 1.0 if duration > 3.0 else 0.0
    scope_end = min(duration, scope_start + 3.0) if duration > scope_start else duration
    if scope_end <= scope_start:
        return 0.0, duration
    return scope_start, scope_end


def _gps_validation_scope(log: Any, elapsed_values: list[float]) -> tuple[float, float]:
    duration = max(elapsed_values) if elapsed_values else 0.0
    valid_elapsed = _valid_gps_elapsed_values(log, elapsed_values)
    if len(valid_elapsed) < 2:
        return _fallback_scope(elapsed_values)

    for index, scope_start in enumerate(valid_elapsed[:-1]):
        scope_end = min(duration, scope_start + 3.0)
        if scope_end <= scope_start:
            continue
        if any(scope_start <= elapsed <= scope_end for elapsed in valid_elapsed[index + 1 :]):
            return scope_start, scope_end

    scope_start = valid_elapsed[0]
    scope_end = valid_elapsed[-1]
    if scope_end > scope_start:
        return scope_start, scope_end
    return _fallback_scope(elapsed_values)


def validate_gps_map_runtime(
    log_path: Path,
    color_column: str | None,
    output: Path,
    state_root: Path | None,
    width: int,
    height: int,
    full_flight: bool = False,
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
    scope_start, scope_end = (0.0, max(elapsed_values)) if full_flight else _gps_validation_scope(log, elapsed_values)
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
        wall_layers = _run_js(page, "window.__sloppyDebugMap.getStyle().layers.filter(layer => layer.type === 'fill-extrusion').map(layer => layer.id);")
        if wall_layers != []:
            raise AssertionError(f"Flight path contains unintended solid wall layers: {wall_layers}")
    except AssertionError:
        view.close()
        raise

    vendor_security = _validate_vendor_attribution_security(page, "__sloppyDebugMap")
    ground_measurement = _validate_ground_measurement(page)

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
    if not isinstance(after_fit_state, dict):
        view.close()
        raise AssertionError(f"GPS map returned no stable fit state: {after_fit_state}")

    _wait_for_basemap_tiles(page, "__sloppyDebugMap")
    before_imagery_state = _run_js(page, "window.sloppyGpsMap.getState();")
    if not isinstance(before_imagery_state, dict):
        view.close()
        raise AssertionError(
            f"GPS map returned no settled pre-imagery state: {before_imagery_state}"
        )
    osm_image = view.grab().toImage().copy()

    _run_js(
        page,
        "window.sloppyGpsMap.setBasemap('imagery');"
        "window.sloppyGpsMap.setImageryOpacity(0.8); true;",
    )
    _wait_for_basemap_tiles(page, "__sloppyDebugMap")
    imagery_state = _run_js(page, "window.sloppyGpsMap.getState();")
    imagery_basemap = imagery_state.get("basemap") if isinstance(imagery_state, dict) else None
    if not isinstance(imagery_basemap, dict):
        view.close()
        raise AssertionError(f"GPS map did not report imagery state: {imagery_state}")
    if imagery_basemap.get("selected") != "imagery" or abs(
        float(imagery_basemap.get("imageryOpacity") or 0) - 0.8
    ) > 1e-9:
        view.close()
        raise AssertionError(f"GPS imagery controls were not applied: {imagery_state}")
    if imagery_basemap.get("provider") not in {"naip", "gibs"}:
        view.close()
        raise AssertionError(f"GPS imagery did not retain a usable provider: {imagery_state}")
    if not _camera_matches(imagery_state.get("camera"), before_imagery_state.get("camera")):
        view.close()
        raise AssertionError(
            f"GPS imagery switch moved the camera: before={before_imagery_state.get('camera')}; "
            f"after={imagery_state.get('camera')}"
        )
    if imagery_state.get("cursor") != before_imagery_state.get("cursor"):
        view.close()
        raise AssertionError(f"GPS imagery switch changed the cursor: {imagery_state}")
    if imagery_state.get("playback") != before_imagery_state.get("playback"):
        view.close()
        raise AssertionError(f"GPS imagery switch changed playback: {imagery_state}")
    imagery_image = view.grab().toImage().copy()
    try:
        imagery_difference = _assert_imagery_pixels(
            osm_image,
            imagery_image,
            "GPS NAIP imagery",
        )
    except AssertionError:
        view.close()
        raise

    naip_fallback_state = _run_js(
        page,
        "handleBasemapError({sourceId:'usgs-naip-raster-source'});"
        "window.sloppyGpsMap.getState();",
    )
    naip_fallback_basemap = (
        naip_fallback_state.get("basemap") if isinstance(naip_fallback_state, dict) else None
    )
    if not isinstance(naip_fallback_basemap, dict) or naip_fallback_basemap.get("provider") != "gibs":
        view.close()
        raise AssertionError(f"GPS map did not fall back from NAIP to GIBS: {naip_fallback_state}")
    _wait_for_basemap_tiles(page, "__sloppyDebugMap")
    gibs_image = view.grab().toImage().copy()
    try:
        gibs_difference = _assert_imagery_pixels(
            osm_image,
            gibs_image,
            "GPS NASA GIBS fallback",
        )
        naip_contribution = _assert_imagery_pixels(
            gibs_image,
            imagery_image,
            "GPS NAIP contribution over NASA GIBS",
        )
    except AssertionError:
        view.close()
        raise

    osm_fallback_state = _run_js(
        page,
        "handleBasemapError({sourceId:'nasa-gibs-raster-source'});"
        "window.sloppyGpsMap.getState();",
    )
    osm_fallback_basemap = (
        osm_fallback_state.get("basemap") if isinstance(osm_fallback_state, dict) else None
    )
    if not isinstance(osm_fallback_basemap, dict) or osm_fallback_basemap.get("provider") != "osmFallback":
        view.close()
        raise AssertionError(f"GPS map did not fall back from GIBS to OSM: {osm_fallback_state}")

    _run_js(page, "window.sloppyGpsMap.setBasemap('imagery'); true;")
    _wait_for_basemap_tiles(page, "__sloppyDebugMap")
    imagery_retry_state = _run_js(page, "window.sloppyGpsMap.getState();")
    retry_basemap = (
        imagery_retry_state.get("basemap") if isinstance(imagery_retry_state, dict) else None
    )
    if not isinstance(retry_basemap, dict) or retry_basemap.get("provider") not in {"naip", "gibs"}:
        view.close()
        raise AssertionError(f"GPS imagery providers did not recover on retry: {imagery_retry_state}")

    imagery_zoom = _validate_imagery_zoom(page, view, output)

    outside_naip = _validate_outside_naip_fallback(
        page, view, "sloppyGpsMap", "__sloppyDebugMap",
        output.with_stem(output.stem + "-global-gibs"),
    )

    overlay_layout = _assert_gps_overlay_layout(page)
    view.resize(480, 360)
    _run_event_loop(250)
    narrow_overlay_layout = _assert_gps_overlay_layout(page)
    _run_js(page, "window.sloppyGpsMap.setMeasurementActive(true); true;")
    _run_event_loop(100)
    narrow_measurement_layout = _assert_gps_overlay_layout(page)
    _run_js(page, "window.sloppyGpsMap.setMeasurementActive(false); true;")
    view.grab().save(str(output.with_stem(output.stem + "-narrow")))
    view.resize(width, height)
    _run_event_loop(250)
    _run_js(page, "window.sloppyGpsMap.refresh({fit:true}); true;")
    _wait_for_basemap_tiles(page, "__sloppyDebugMap")

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
        "vendorSecurity": vendor_security,
        "groundMeasurement": ground_measurement,
        "imageryZoom": imagery_zoom,
        "overlayLayout": overlay_layout,
        "narrowOverlayLayout": narrow_overlay_layout,
        "narrowMeasurementLayout": narrow_measurement_layout,
        "outsideNaip": outside_naip,
        "log": str(log_path),
        "html": str(html_path),
        "screenshot": str(output),
        "loaded": True,
        "initial": initial_state,
        "afterCursor": cursor_state,
        "afterCamera": after_camera_state,
        "afterFit": after_fit_state,
        "beforeImagery": before_imagery_state,
        "imagery": imagery_state,
        "imageryDifference": imagery_difference,
        "naipContribution": naip_contribution,
        "naipFallback": naip_fallback_state,
        "gibsDifference": gibs_difference,
        "osmFallback": osm_fallback_state,
        "imageryRetry": imagery_retry_state,
        "image": metrics,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the MapLibre 3D GPS map in a live Qt WebEngine view.")
    parser.add_argument("log", type=Path)
    parser.add_argument("--color-column")
    parser.add_argument("--full-flight", action="store_true", help="Render the entire flight instead of a short scope.")
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
        full_flight=args.full_flight,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
