"""Offline MapLibre HTML/JS template for the GPS flight path view."""

from __future__ import annotations

import html
import json
from pathlib import Path

MAPLIBRE_VERSION = "5.24.0"
OPENSTREETMAP_RASTER_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
OPENSTREETMAP_RASTER_TILE_MAX_ZOOM = 19
MAP_MAX_ZOOM = 19
MAP_FIT_MAX_ZOOM = 17
MAP_MAX_PITCH = 85
ALTITUDE_EXAGGERATION = 5.0
ALTITUDE_FLOOR_METERS = 12.0


def _asset_uri(filename: str) -> str:
    # MapLibre assets are bundled with the package; the WebEngine view loads
    # them from local file URIs so the map works without networked assets.
    asset_path = Path(__file__).resolve().parent / "assets" / "maplibre" / filename
    return asset_path.as_uri()


def _gps_message_html(message: str, dark: bool) -> str:
    background = "#1f242b" if dark else "#ffffff"
    color = "#e5e7eb" if dark else "#1f2937"
    border = "#3a414d" if dark else "#d1d5db"
    return f"""
<html>
<body style="margin:0;background:{background};color:{color};font-family:Arial,sans-serif;">
  <div style="height:100vh;display:flex;align-items:center;justify-content:center;text-align:center;">
    <div style="border:1px solid {border};border-radius:6px;padding:18px 22px;max-width:520px;">
      {html.escape(message)}
    </div>
  </div>
</body>
</html>
"""


def build_gps_map_html(payload: dict[str, object], dark: bool = True) -> str:
    if payload.get("status") != "ok":
        return _gps_message_html(str(payload.get("message") or "No GPS path to display."), dark)

    background = "#1f242b" if dark else "#ffffff"
    panel_bg = "rgba(21,24,29,0.88)" if dark else "rgba(255,255,255,0.92)"
    panel_fg = "#e5e7eb" if dark else "#1f2937"
    border = "rgba(255,255,255,0.18)" if dark else "rgba(0,0,0,0.18)"
    css_uri = html.escape(_asset_uri("maplibre-gl.css"), quote=True)
    js_uri = html.escape(_asset_uri("maplibre-gl-csp.js"), quote=True)
    worker_uri_json = json.dumps(_asset_uri("maplibre-gl-csp-worker.js"))
    data_json = json.dumps(payload, allow_nan=False)

    # The document is a large inline template because the WebEngine path needs
    # to stay self-contained and work even when the app is packaged.
    document = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="__MAPLIBRE_CSS_URI__">
  <script src="__MAPLIBRE_JS_URI__"></script>
  <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
  <style>
    html, body {
      width: 100%;
      height: 100%;
      margin: 0;
      padding: 0;
      overflow: hidden;
      background: __BACKGROUND__;
      font-family: Arial, sans-serif;
    }
    body {
      position: relative;
    }
    #map {
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      background: __BACKGROUND__;
    }
    #flightCanvas {
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      z-index: 5;
      pointer-events: none;
    }
    #legend {
      position: absolute;
      left: 12px;
      bottom: 30px;
      min-width: 260px;
      max-width: 380px;
      color: __PANEL_FG__;
      background: __PANEL_BG__;
      border: 1px solid __BORDER__;
      border-radius: 6px;
      padding: 10px 12px;
      box-sizing: border-box;
      font-size: 12px;
      z-index: 10;
    }
    #legendTitle {
      font-weight: 700;
      margin-bottom: 7px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    #gradientBar {
      height: 12px;
      border-radius: 4px;
      border: 1px solid __BORDER__;
      margin-bottom: 5px;
    }
    #legendLabels {
      display: flex;
      justify-content: space-between;
      gap: 8px;
    }
    #statusOverlay {
      position: absolute;
      z-index: 12;
      font-size: 11px;
      line-height: 1.3;
      color: __PANEL_FG__;
      background: __PANEL_BG__;
      border: 1px solid __BORDER__;
      border-radius: 5px;
      padding: 6px 8px;
      box-sizing: border-box;
      pointer-events: none;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      left: 8px;
      top: 8px;
      min-width: 160px;
      max-width: 360px;
    }
    #statusOverlay[data-kind="error"] {
      border-color: rgba(239,68,68,0.85);
      color: #fecaca;
      background: rgba(127,29,29,0.9);
    }
    #statusOverlay[data-kind="ok"] {
      color: #dcfce7;
      background: rgba(20,83,45,0.88);
      border-color: rgba(74,222,128,0.65);
    }
    #currentPointReadout {
      position: absolute;
      z-index: 13;
      min-width: 150px;
      max-width: 260px;
      color: __PANEL_FG__;
      background: __PANEL_BG__;
      border: 1px solid __BORDER__;
      border-radius: 6px;
      padding: 7px 9px;
      box-sizing: border-box;
      pointer-events: none;
      font-size: 12px;
      line-height: 1.35;
      box-shadow: 0 8px 24px rgba(0,0,0,0.25);
    }
    #currentPointReadout::after {
      content: "";
      position: absolute;
      left: 18px;
      bottom: -7px;
      width: 12px;
      height: 12px;
      transform: rotate(45deg);
      background: __PANEL_BG__;
      border-right: 1px solid __BORDER__;
      border-bottom: 1px solid __BORDER__;
    }
    .readoutTitle {
      font-weight: 700;
      margin-bottom: 3px;
    }
    .readoutRow {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      white-space: nowrap;
    }
    .readoutLabel {
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .readoutValue {
      font-variant-numeric: tabular-nums;
    }
    #cameraControls {
      position: absolute;
      top: 10px;
      left: 50%;
      transform: translateX(-50%);
      z-index: 11;
      display: flex;
      gap: 4px;
      padding: 4px;
      color: __PANEL_FG__;
      background: __PANEL_BG__;
      border: 1px solid __BORDER__;
      border-radius: 6px;
      box-sizing: border-box;
    }
    #cameraControls button {
      min-width: 54px;
      height: 28px;
      padding: 0 10px;
      border: 1px solid transparent;
      border-radius: 4px;
      color: __PANEL_FG__;
      background: transparent;
      font: 12px Arial, sans-serif;
      cursor: pointer;
    }
    #cameraControls button:hover,
    #cameraControls button:focus {
      border-color: __BORDER__;
      outline: none;
    }
    #cameraControls button[aria-pressed="true"] {
      color: #ffffff;
      background: #2f80ed;
      border-color: #2f80ed;
    }
    #mapAttribution {
      position: absolute;
      right: 8px;
      bottom: 2px;
      z-index: 10;
      font-size: 11px;
      color: #111827;
      background: rgba(255,255,255,0.84);
      padding: 2px 4px;
      border-radius: 3px;
    }
    #mapAttribution a {
      color: #0645ad;
    }
    #playbackOverlay {
      position: absolute;
      left: 50%;
      bottom: 26px;
      transform: translateX(-50%);
      z-index: 12;
      width: min(760px, calc(100% - 32px));
      display: grid;
      grid-template-columns: auto 52px minmax(120px, 1fr) 52px auto;
      align-items: center;
      gap: 8px;
      padding: 8px 10px;
      box-sizing: border-box;
      color: __PANEL_FG__;
      background: __PANEL_BG__;
      border: 1px solid __BORDER__;
      border-radius: 7px;
      box-shadow: 0 10px 28px rgba(0,0,0,0.28);
    }
    #playbackOverlay[hidden] {
      display: none;
    }
    #playPauseButton {
      width: 58px;
      height: 30px;
      border: 1px solid __BORDER__;
      border-radius: 4px;
      color: #ffffff;
      background: #2f80ed;
      font: 15px Arial, sans-serif;
      cursor: pointer;
    }
    #playbackSlider {
      width: 100%;
      accent-color: #2f80ed;
    }
    #playbackCurrent,
    #playbackDuration {
      font-size: 12px;
      font-variant-numeric: tabular-nums;
      text-align: center;
      white-space: nowrap;
    }
    #playbackSpeed {
      height: 30px;
      color: __PANEL_FG__;
      background: rgba(0,0,0,0.16);
      border: 1px solid __BORDER__;
      border-radius: 4px;
      font: 12px Arial, sans-serif;
    }
  </style>
</head>
<body>
  <div id="map"></div>
  <canvas id="flightCanvas"></canvas>
  <div id="currentPointReadout" hidden></div>
  <div id="legend" hidden>
    <div id="legendTitle"></div>
    <div id="gradientBar"></div>
    <div id="legendLabels">
      <span id="legendMin"></span>
      <span id="legendMid"></span>
      <span id="legendMax"></span>
    </div>
  </div>
  <div id="statusOverlay" hidden></div>
  <div id="cameraControls" aria-label="Map camera">
    <button type="button" data-camera-mode="top" title="Top-down map view">Top</button>
    <button type="button" data-camera-mode="orbit" title="Oblique 3D flight view" aria-pressed="true">3D</button>
    <button type="button" data-camera-mode="ground" title="Near-ground flight path view">Ground</button>
  </div>
  <div id="playbackOverlay" aria-label="GPS playback" hidden>
    <button type="button" id="playPauseButton" title="Play or pause flight playback">Play</button>
    <span id="playbackCurrent">0:00</span>
    <input type="range" id="playbackSlider" min="0" max="1000" value="0" step="1" aria-label="Flight timeline">
    <span id="playbackDuration">0:00</span>
    <select id="playbackSpeed" aria-label="Playback speed">
      <option value="0.25">.25x</option>
      <option value="0.5">.5x</option>
      <option value="1" selected>1x</option>
      <option value="2">2x</option>
      <option value="5">5x</option>
      <option value="10">10x</option>
    </select>
  </div>
  <div id="mapAttribution">
    Map tiles &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>
  </div>
  <script>
    maplibregl.workerUrl = __MAPLIBRE_WORKER_URI__;
    const flightData = __FLIGHT_DATA__;
    const mapMaxZoom = Number(flightData.mapMaxZoom) || 19;
    const mapFitMaxZoom = Number(flightData.mapFitMaxZoom) || Math.min(17, mapMaxZoom);
    const mapMaxPitch = Number(flightData.mapMaxPitch) || 85;
    const rasterTileUrls = Array.isArray(flightData.rasterTileUrls) && flightData.rasterTileUrls.length
      ? flightData.rasterTileUrls
      : ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"];
    const rasterTileMaxZoom = Number(flightData.rasterTileMaxZoom) || 19;
    const pathParts = Array.isArray(flightData.pathParts) && flightData.pathParts.length
      ? flightData.pathParts
      : [flightData.points];
    const altitudeStats = flightData.altitudeStats || {};
    const altitudeBase = Number.isFinite(Number(altitudeStats.baseMeters))
      ? Number(altitudeStats.baseMeters)
      : 0;
    const altitudeScale = Number.isFinite(Number(flightData.altitudeScale))
      ? Number(flightData.altitudeScale)
      : 5;
    const altitudeFloorMeters = Number.isFinite(Number(flightData.altitudeFloorMeters))
      ? Number(flightData.altitudeFloorMeters)
      : 12;
    const timeline = flightData.timeline || {};
    const timelineEnabled = !!timeline.enabled;
    const timelineDurationSeconds = Number.isFinite(Number(timeline.durationSeconds))
      ? Math.max(0, Number(timeline.durationSeconds))
      : 0;
    const pathRibbonWidthMeters = 24;
    const legendContainer = document.getElementById("legend");
    const legendTitle = document.getElementById("legendTitle");
    const gradientBar = document.getElementById("gradientBar");
    const legendMin = document.getElementById("legendMin");
    const legendMid = document.getElementById("legendMid");
    const legendMax = document.getElementById("legendMax");
    const statusOverlay = document.getElementById("statusOverlay");
    const cameraControls = document.getElementById("cameraControls");
    const flightCanvas = document.getElementById("flightCanvas");
    const flightCanvasContext = flightCanvas ? flightCanvas.getContext("2d") : null;
    const currentPointReadout = document.getElementById("currentPointReadout");
    const playbackOverlay = document.getElementById("playbackOverlay");
    const playPauseButton = document.getElementById("playPauseButton");
    const playbackSlider = document.getElementById("playbackSlider");
    const playbackCurrent = document.getElementById("playbackCurrent");
    const playbackDuration = document.getElementById("playbackDuration");
    const playbackSpeed = document.getElementById("playbackSpeed");
    let gpsBridge = null;
    let statusHideTimer = null;
    let map = null;
    let flightBounds = null;
    let initialBearing = 0;
    let flightLayersAdded = false;
    let elevationLayerReady = false;
    let elevationLayerError = "";
    let canvasPathReady = false;
    let extrusionLayerReady = false;
    let ribbonVertexCount = 0;
    let guideVertexCount = 0;
    let activeCameraMode = "orbit";
    let currentCursor = {
      index: 0,
      row: 1,
      elapsedSeconds: 0,
      durationSeconds: timelineDurationSeconds,
      playing: false,
      speed: 1,
      values: []
    };
    let elevationMatrixSource = "";
    let elevationRenderArgKeys = [];
    let canvasDrawFrame = null;
    let viewportRefreshFrame = null;

    function finiteNumber(value) {
      return value !== null && value !== "" && Number.isFinite(Number(value));
    }

    function clampNumber(value, minimum, maximum) {
      const number = Number(value);
      if (!Number.isFinite(number)) {
        return minimum;
      }
      return Math.max(minimum, Math.min(maximum, number));
    }

    function formatElapsed(seconds) {
      const safeSeconds = Math.max(0, Math.floor(Number(seconds) || 0));
      const hours = Math.floor(safeSeconds / 3600);
      const minutes = Math.floor((safeSeconds % 3600) / 60);
      const remainingSeconds = safeSeconds % 60;
      if (hours > 0) {
        return `${hours}:${String(minutes).padStart(2, "0")}:${String(remainingSeconds).padStart(2, "0")}`;
      }
      return `${minutes}:${String(remainingSeconds).padStart(2, "0")}`;
    }

    function formatReadoutValue(value) {
      if (value === null || value === undefined || value === "") {
        return "";
      }
      const number = Number(value);
      if (Number.isFinite(number)) {
        if (Math.abs(number) >= 1000 || (Math.abs(number) < 0.01 && number !== 0)) {
          return number.toPrecision(4);
        }
        return number.toFixed(3).replace(/\.?0+$/, "");
      }
      return String(value);
    }

    function escapeHtml(value) {
      return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }

    function connectGpsBridge() {
      if (!window.qt || !window.qt.webChannelTransport || typeof QWebChannel === "undefined") {
        return;
      }
      new QWebChannel(window.qt.webChannelTransport, (channel) => {
        gpsBridge = channel.objects.gpsBridge || null;
      });
    }

    function callGpsBridge(method, value) {
      if (!gpsBridge || typeof gpsBridge[method] !== "function") {
        return false;
      }
      gpsBridge[method](value);
      return true;
    }

    function debugMapState() {
      const layerIds = [
        "osm-raster-base",
        "flight-underlay",
        "flight-segments",
        "flight-extrusions",
        "flight-marker-circles",
        "flight-marker-labels",
        "flight-elevation-layer"
      ];
      const sourceIds = [
        "osm-raster-source",
        "flight-underlay-source",
        "flight-segments-source",
        "flight-extrusions-source",
        "flight-markers"
      ];
      const pathReady = extrusionLayerReady || elevationLayerReady || canvasPathReady;
      const state = {
        ready: flightLayersAdded && pathReady,
        state: flightLayersAdded && pathReady ? "ready" : (elevationLayerError ? "degraded" : "loading"),
        initialized: !!map,
        styleLoaded: !!(map && map.isStyleLoaded && map.isStyleLoaded()),
        points: Array.isArray(flightData.points) ? flightData.points.length : 0,
        segments: Array.isArray(flightData.segments) ? flightData.segments.length : 0,
        pathParts: pathParts.length,
        canvasPathReady,
        extrusionLayerReady,
        elevationLayerReady,
        elevationLayerError,
        elevationMatrixSource,
        elevationRenderArgKeys,
        webglPathReady: elevationLayerReady,
        native3dPathReady: extrusionLayerReady,
        ribbonVertexCount,
        guideVertexCount,
        cameraMode: activeCameraMode,
        cursor: {
          index: currentCursor.index,
          row: currentCursor.row,
          elapsedSeconds: currentCursor.elapsedSeconds,
          durationSeconds: currentCursor.durationSeconds,
          values: Array.isArray(currentCursor.values) ? currentCursor.values.length : 0
        },
        playback: {
          enabled: timelineEnabled,
          playing: !!currentCursor.playing,
          speed: currentCursor.speed,
          durationSeconds: timelineDurationSeconds
        },
        camera: map ? {
          zoom: map.getZoom(),
          pitch: map.getPitch(),
          bearing: map.getBearing(),
          maxZoom: mapMaxZoom,
          fitMaxZoom: mapFitMaxZoom,
          maxPitch: mapMaxPitch
        } : null,
        altitude: {
          label: altitudeStats.label || flightData.altitudeLabel || "Altitude",
          minimum: finiteNumber(altitudeStats.minimum) ? Number(altitudeStats.minimum) : null,
          maximum: finiteNumber(altitudeStats.maximum) ? Number(altitudeStats.maximum) : null,
          baseMeters: altitudeBase,
          scale: altitudeScale,
          floorMeters: altitudeFloorMeters
        },
        layers: {},
        sources: {},
        status: statusOverlay && !statusOverlay.hidden ? statusOverlay.textContent : ""
      };
      if (document.body) {
        document.body.dataset.mapState = state.state;
      }
      if (map) {
        layerIds.forEach((id) => {
          state.layers[id] = !!map.getLayer(id);
        });
        sourceIds.forEach((id) => {
          state.sources[id] = !!map.getSource(id);
        });
      }
      window.__sloppyDebugMapState = state;
      return state;
    }

    function setStatus(message, kind) {
      if (!statusOverlay) return;
      if (statusHideTimer) {
        clearTimeout(statusHideTimer);
        statusHideTimer = null;
      }
      if (!message) {
        statusOverlay.hidden = true;
        statusOverlay.textContent = "";
        statusOverlay.dataset.kind = "";
        debugMapState();
        return;
      }
      statusOverlay.hidden = false;
      statusOverlay.dataset.kind = kind || "info";
      statusOverlay.textContent = message;
      debugMapState();
    }

    function transientStatus(message, kind, timeoutMs) {
      setStatus(message, kind);
      if ((kind || "info") === "error") {
        return;
      }
      statusHideTimer = setTimeout(() => {
        if (statusOverlay && statusOverlay.dataset.kind === (kind || "info")) {
          setStatus("", "");
        }
      }, timeoutMs || 1800);
    }

    function renderLegend() {
      const legend = flightData.legend;
      if (!legend || !legend.enabled) {
        legendContainer.hidden = true;
        return;
      }
      legendContainer.hidden = false;
      legendTitle.textContent = `Color by ${legend.label}`;
      gradientBar.style.background = `linear-gradient(90deg, ${legend.lowColor}, ${legend.highColor})`;
      legendMin.textContent = legend.minLabel;
      legendMid.textContent = legend.midLabel || "";
      legendMax.textContent = legend.maxLabel;
    }

    function coordinatesFromPart(part) {
      if (!Array.isArray(part)) {
        return [];
      }
      return part
        .filter((point) => point && finiteNumber(point.lon) && finiteNumber(point.lat))
        .map((point) => [Number(point.lon), Number(point.lat)]);
    }

    function coordinateFromPoint(point) {
      if (!point || !finiteNumber(point.lon) || !finiteNumber(point.lat)) {
        return null;
      }
      return [Number(point.lon), Number(point.lat)];
    }

    function allPathPoints() {
      return pathParts.flatMap((part) => Array.isArray(part) ? part : []);
    }

    function nearestPointForElapsed(elapsedSeconds) {
      const points = allPathPoints();
      if (!points.length) {
        return null;
      }
      let nearest = points[0];
      let nearestDistance = Math.abs(Number(nearest.elapsedSeconds || 0) - elapsedSeconds);
      points.forEach((point) => {
        const distance = Math.abs(Number(point.elapsedSeconds || 0) - elapsedSeconds);
        if (distance < nearestDistance) {
          nearest = point;
          nearestDistance = distance;
        }
      });
      return nearest;
    }

    function interpolatedPointForElapsed(elapsedSeconds) {
      const target = Number(elapsedSeconds);
      if (!Number.isFinite(target)) {
        return nearestPointForElapsed(0);
      }
      for (const part of pathParts) {
        if (!Array.isArray(part) || !part.length) {
          continue;
        }
        const first = part[0];
        const last = part[part.length - 1];
        const firstElapsed = Number(first.elapsedSeconds || 0);
        const lastElapsed = Number(last.elapsedSeconds || firstElapsed);
        if (target < firstElapsed || target > lastElapsed) {
          continue;
        }
        for (let index = 0; index < part.length - 1; index += 1) {
          const left = part[index];
          const right = part[index + 1];
          const leftElapsed = Number(left.elapsedSeconds || 0);
          const rightElapsed = Number(right.elapsedSeconds || leftElapsed);
          if (target < leftElapsed || target > rightElapsed) {
            continue;
          }
          const span = rightElapsed - leftElapsed;
          const amount = span > 0 ? (target - leftElapsed) / span : 0;
          const nearest = amount <= 0.5 ? left : right;
          return {
            lat: Number(left.lat) + (Number(right.lat) - Number(left.lat)) * amount,
            lon: Number(left.lon) + (Number(right.lon) - Number(left.lon)) * amount,
            alt: Number(left.alt || 0) + (Number(right.alt || 0) - Number(left.alt || 0)) * amount,
            elapsedSeconds: target,
            row: nearest.row
          };
        }
        return nearestPointForElapsed(target);
      }
      return nearestPointForElapsed(target);
    }

    function updatePlaybackControls() {
      if (!playbackOverlay || !playbackSlider || !playbackCurrent || !playbackDuration || !playPauseButton) {
        return;
      }
      playbackOverlay.hidden = !timelineEnabled;
      const duration = Math.max(0, Number(currentCursor.durationSeconds || timelineDurationSeconds) || 0);
      const elapsed = clampNumber(currentCursor.elapsedSeconds, 0, duration || 0);
      const sliderValue = duration > 0 ? Math.round((elapsed / duration) * 1000) : 0;
      playbackSlider.value = String(sliderValue);
      playbackCurrent.textContent = formatElapsed(elapsed);
      playbackDuration.textContent = formatElapsed(duration);
      playPauseButton.textContent = currentCursor.playing ? "Pause" : "Play";
      if (playbackSpeed) {
        playbackSpeed.value = String(currentCursor.speed || 1);
      }
    }

    function renderCurrentReadout(projected) {
      if (!currentPointReadout || !projected) {
        if (currentPointReadout) {
          currentPointReadout.hidden = true;
        }
        return;
      }
      const values = Array.isArray(currentCursor.values) ? currentCursor.values : [];
      const rows = values
        .filter((entry) => entry && entry.label)
        .map((entry) => (
          `<div class="readoutRow"><span class="readoutLabel">${escapeHtml(entry.label)}</span>` +
          `<span class="readoutValue">${escapeHtml(formatReadoutValue(entry.value))}</span></div>`
        ));
      currentPointReadout.innerHTML =
        `<div class="readoutTitle">${formatElapsed(currentCursor.elapsedSeconds || 0)} | Row ${currentCursor.row || 1}</div>` +
        rows.join("");
      const left = Math.max(8, Math.min(window.innerWidth - 280, projected.x + 12));
      const top = Math.max(8, Math.min(window.innerHeight - 160, projected.y - 76));
      currentPointReadout.style.left = `${left}px`;
      currentPointReadout.style.top = `${top}px`;
      currentPointReadout.hidden = false;
    }

    function drawCurrentCursorMarker() {
      if (!flightCanvasContext || !map) {
        return false;
      }
      const point = interpolatedPointForElapsed(currentCursor.elapsedSeconds || 0);
      const projected = point ? projectedPoint(point, true) : null;
      if (!projected) {
        renderCurrentReadout(null);
        return false;
      }
      flightCanvasContext.save();
      flightCanvasContext.fillStyle = "#f2c94c";
      flightCanvasContext.strokeStyle = "#111827";
      flightCanvasContext.lineWidth = 3;
      flightCanvasContext.beginPath();
      flightCanvasContext.arc(projected.x, projected.y, 9, 0, Math.PI * 2);
      flightCanvasContext.fill();
      flightCanvasContext.stroke();
      flightCanvasContext.strokeStyle = "#ffffff";
      flightCanvasContext.lineWidth = 2;
      flightCanvasContext.beginPath();
      flightCanvasContext.moveTo(projected.x - 13, projected.y);
      flightCanvasContext.lineTo(projected.x + 13, projected.y);
      flightCanvasContext.moveTo(projected.x, projected.y - 13);
      flightCanvasContext.lineTo(projected.x, projected.y + 13);
      flightCanvasContext.stroke();
      flightCanvasContext.restore();
      renderCurrentReadout(projected);
      return true;
    }

    function setCursor(cursor) {
      const next = cursor || {};
      const duration = Number.isFinite(Number(next.durationSeconds))
        ? Math.max(0, Number(next.durationSeconds))
        : timelineDurationSeconds;
      currentCursor = Object.assign({}, currentCursor, {
        index: Number.isFinite(Number(next.index)) ? Number(next.index) : currentCursor.index,
        row: Number.isFinite(Number(next.row)) ? Number(next.row) : currentCursor.row,
        elapsedSeconds: clampNumber(next.elapsedSeconds, 0, duration || timelineDurationSeconds || 0),
        durationSeconds: duration,
        playing: next.playing === undefined ? currentCursor.playing : !!next.playing,
        speed: Number.isFinite(Number(next.speed)) ? Number(next.speed) : currentCursor.speed,
        values: Array.isArray(next.values) ? next.values : currentCursor.values
      });
      updatePlaybackControls();
      scheduleFlightCanvasDraw();
      debugMapState();
      return true;
    }

    function setPlayback(playback) {
      return setCursor(Object.assign({}, playback || {}, { values: currentCursor.values }));
    }

    function extrusionCoordinatesForSegment(leftPoint, rightPoint, widthMeters) {
      const left = coordinateFromPoint(leftPoint);
      const right = coordinateFromPoint(rightPoint);
      if (!left || !right) {
        return null;
      }
      const midLatRadians = ((left[1] + right[1]) / 2) * Math.PI / 180;
      const metersPerDegreeLat = 110540;
      const metersPerDegreeLon = Math.max(1, 111320 * Math.cos(midLatRadians));
      const dx = (right[0] - left[0]) * metersPerDegreeLon;
      const dy = (right[1] - left[1]) * metersPerDegreeLat;
      const length = Math.hypot(dx, dy);
      if (!Number.isFinite(length) || length <= 0) {
        return null;
      }
      const nx = -dy / length * widthMeters / 2;
      const ny = dx / length * widthMeters / 2;
      const offset = (coordinate, sign) => [
        coordinate[0] + (nx * sign) / metersPerDegreeLon,
        coordinate[1] + (ny * sign) / metersPerDegreeLat
      ];
      const leftA = offset(left, 1);
      const rightA = offset(right, 1);
      const rightB = offset(right, -1);
      const leftB = offset(left, -1);
      return [[leftA, rightA, rightB, leftB, leftA]];
    }

    function altitudeRenderMeters(point) {
      if (!point || !finiteNumber(point.alt)) {
        return altitudeFloorMeters;
      }
      return Math.max(0, (Number(point.alt) - altitudeBase) * altitudeScale + altitudeFloorMeters);
    }

    function colorToRgba(color, alpha) {
      const fallback = [0.33, 0.85, 0.47, alpha];
      if (typeof color !== "string" || !/^#[0-9a-fA-F]{6}$/.test(color)) {
        return fallback;
      }
      return [
        parseInt(color.slice(1, 3), 16) / 255,
        parseInt(color.slice(3, 5), 16) / 255,
        parseInt(color.slice(5, 7), 16) / 255,
        alpha
      ];
    }

    function mercatorCoordinate(point, altitudeMeters) {
      return maplibregl.MercatorCoordinate.fromLngLat(
        { lng: Number(point.lon), lat: Number(point.lat) },
        altitudeMeters
      );
    }

    function mercatorPoint(point, altitudeMeters) {
      const coordinate = mercatorCoordinate(point, altitudeMeters);
      return [coordinate.x, coordinate.y, coordinate.z];
    }

    function buildElevationRenderData() {
      const ribbonPositions = [];
      const ribbonColors = [];
      const shadowPositions = [];
      const shadowColors = [];
      const guidePositions = [];
      const guideColors = [];
      const guideColor = [1, 1, 1, 0.35];
      const addRibbonQuad = (positions, colors, start, end, color, widthMeters) => {
        const dx = end.x - start.x;
        const dy = end.y - start.y;
        const length = Math.hypot(dx, dy);
        if (!Number.isFinite(length) || length <= 0) {
          return;
        }
        const widthUnits = ((start.meterInMercatorCoordinateUnits() + end.meterInMercatorCoordinateUnits()) / 2)
          * Math.max(1, widthMeters);
        const halfWidth = widthUnits / 2;
        const nx = -dy / length * halfWidth;
        const ny = dx / length * halfWidth;
        const leftStart = [start.x + nx, start.y + ny, start.z];
        const rightStart = [start.x - nx, start.y - ny, start.z];
        const leftEnd = [end.x + nx, end.y + ny, end.z];
        const rightEnd = [end.x - nx, end.y - ny, end.z];
        positions.push(
          ...leftStart, ...rightStart, ...leftEnd,
          ...rightStart, ...rightEnd, ...leftEnd
        );
        for (let index = 0; index < 6; index += 1) {
          colors.push(...color);
        }
      };

      flightData.segments.forEach((segment) => {
        if (!segment || !segment.left || !segment.right) {
          return;
        }
        const left = coordinateFromPoint(segment.left);
        const right = coordinateFromPoint(segment.right);
        if (!left || !right) {
          return;
        }
        const color = colorToRgba(segment.color, 0.98);
        const shadowColor = colorToRgba(segment.color, 0.24);
        const leftGround = mercatorCoordinate(segment.left, 0);
        const rightGround = mercatorCoordinate(segment.right, 0);
        const leftElevated = mercatorCoordinate(segment.left, altitudeRenderMeters(segment.left));
        const rightElevated = mercatorCoordinate(segment.right, altitudeRenderMeters(segment.right));
        addRibbonQuad(shadowPositions, shadowColors, leftGround, rightGround, shadowColor, pathRibbonWidthMeters * 1.5);
        addRibbonQuad(ribbonPositions, ribbonColors, leftElevated, rightElevated, color, pathRibbonWidthMeters);
      });

      const points = Array.isArray(flightData.points) ? flightData.points : [];
      const guideStride = Math.max(1, Math.floor(points.length / 24));
      points.forEach((point, index) => {
        if (index !== 0 && index !== points.length - 1 && index % guideStride !== 0) {
          return;
        }
        const coordinate = coordinateFromPoint(point);
        if (!coordinate) {
          return;
        }
        const ground = mercatorPoint(point, 0);
        const elevated = mercatorPoint(point, altitudeRenderMeters(point));
        guidePositions.push(...ground, ...elevated);
        guideColors.push(...guideColor, ...guideColor);
      });

      return {
        ribbonPositions: new Float32Array(ribbonPositions),
        ribbonColors: new Float32Array(ribbonColors),
        ribbonVertexCount: ribbonPositions.length / 3,
        shadowPositions: new Float32Array(shadowPositions),
        shadowColors: new Float32Array(shadowColors),
        shadowVertexCount: shadowPositions.length / 3,
        guidePositions: new Float32Array(guidePositions),
        guideColors: new Float32Array(guideColors),
        guideVertexCount: guidePositions.length / 3
      };
    }

    function compileShader(gl, type, source) {
      const shader = gl.createShader(type);
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
        const details = gl.getShaderInfoLog(shader) || "unknown shader compile error";
        gl.deleteShader(shader);
        throw new Error(details);
      }
      return shader;
    }

    function createElevationProgram(gl) {
      const vertexShader = compileShader(gl, gl.VERTEX_SHADER, `
        attribute vec3 a_pos;
        attribute vec4 a_color;
        uniform mat4 u_matrix;
        varying vec4 v_color;
        void main() {
          gl_Position = u_matrix * vec4(a_pos, 1.0);
          v_color = a_color;
        }
      `);
      const fragmentShader = compileShader(gl, gl.FRAGMENT_SHADER, `
        precision mediump float;
        varying vec4 v_color;
        void main() {
          gl_FragColor = v_color;
        }
      `);
      const program = gl.createProgram();
      gl.attachShader(program, vertexShader);
      gl.attachShader(program, fragmentShader);
      gl.linkProgram(program);
      gl.deleteShader(vertexShader);
      gl.deleteShader(fragmentShader);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
        const details = gl.getProgramInfoLog(program) || "unknown program link error";
        gl.deleteProgram(program);
        throw new Error(details);
      }
      return program;
    }

    function createArrayBuffer(gl, data) {
      const buffer = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
      return buffer;
    }

    function matrixForUniform(renderArgs) {
      if (renderArgs && typeof renderArgs === "object" && typeof renderArgs.length !== "number") {
        elevationRenderArgKeys = Object.keys(renderArgs).slice(0, 12);
      }
      let matrix = null;
      if (renderArgs && renderArgs.modelViewProjectionMatrix) {
        matrix = renderArgs.modelViewProjectionMatrix;
        elevationMatrixSource = "modelViewProjectionMatrix";
      } else if (renderArgs && renderArgs.defaultProjectionData && renderArgs.defaultProjectionData.mainMatrix) {
        matrix = renderArgs.defaultProjectionData.mainMatrix;
        elevationMatrixSource = "defaultProjectionData.mainMatrix";
      } else if (renderArgs && renderArgs.projectionData && renderArgs.projectionData.mainMatrix) {
        matrix = renderArgs.projectionData.mainMatrix;
        elevationMatrixSource = "projectionData.mainMatrix";
      } else {
        matrix = renderArgs;
        elevationMatrixSource = "legacy-matrix";
      }
      if (!matrix) {
        throw new Error("custom layer render matrix was not provided");
      }
      if (matrix instanceof Float32Array) {
        return matrix;
      }
      if (Array.isArray(matrix)) {
        return new Float32Array(matrix);
      }
      if (typeof matrix.length === "number") {
        const values = new Float32Array(matrix.length);
        for (let index = 0; index < matrix.length; index += 1) {
          values[index] = Number(matrix[index]);
        }
        return values;
      }
      throw new Error("custom layer render matrix was not array-like");
    }

    function makeFlightElevationLayer() {
      const renderData = buildElevationRenderData();
      return {
        id: "flight-elevation-layer",
        type: "custom",
        renderingMode: "3d",

        onAdd: function(_map, gl) {
          this.program = createElevationProgram(gl);
          this.positionLocation = gl.getAttribLocation(this.program, "a_pos");
          this.colorLocation = gl.getAttribLocation(this.program, "a_color");
          this.matrixLocation = gl.getUniformLocation(this.program, "u_matrix");
          this.ribbonPositionBuffer = createArrayBuffer(gl, renderData.ribbonPositions);
          this.ribbonColorBuffer = createArrayBuffer(gl, renderData.ribbonColors);
          this.ribbonVertexCount = renderData.ribbonVertexCount;
          this.shadowPositionBuffer = createArrayBuffer(gl, renderData.shadowPositions);
          this.shadowColorBuffer = createArrayBuffer(gl, renderData.shadowColors);
          this.shadowVertexCount = renderData.shadowVertexCount;
          this.guidePositionBuffer = createArrayBuffer(gl, renderData.guidePositions);
          this.guideColorBuffer = createArrayBuffer(gl, renderData.guideColors);
          this.guideVertexCount = renderData.guideVertexCount;
          ribbonVertexCount = this.ribbonVertexCount;
          guideVertexCount = this.guideVertexCount;
          elevationLayerReady = this.ribbonVertexCount > 0;
          elevationLayerError = "";
          debugMapState();
        },

        render: function(gl, renderArgs) {
          try {
            if (!this.program || !this.ribbonVertexCount) {
              return;
            }
            const matrix = matrixForUniform(renderArgs);
            gl.useProgram(this.program);
            gl.uniformMatrix4fv(this.matrixLocation, false, matrix);
            gl.disable(gl.DEPTH_TEST);
            gl.enable(gl.BLEND);
            gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

            const bindArrays = (positionBuffer, colorBuffer) => {
              gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer);
              gl.enableVertexAttribArray(this.positionLocation);
              gl.vertexAttribPointer(this.positionLocation, 3, gl.FLOAT, false, 0, 0);
              gl.bindBuffer(gl.ARRAY_BUFFER, colorBuffer);
              gl.enableVertexAttribArray(this.colorLocation);
              gl.vertexAttribPointer(this.colorLocation, 4, gl.FLOAT, false, 0, 0);
            };
            const drawTriangles = (positionBuffer, colorBuffer, vertexCount) => {
              if (!vertexCount) {
                return;
              }
              bindArrays(positionBuffer, colorBuffer);
              gl.drawArrays(gl.TRIANGLES, 0, vertexCount);
            };
            const drawLines = (positionBuffer, colorBuffer, vertexCount) => {
              if (!vertexCount) {
                return;
              }
              bindArrays(positionBuffer, colorBuffer);
              gl.drawArrays(gl.LINES, 0, vertexCount);
            };

            drawTriangles(this.shadowPositionBuffer, this.shadowColorBuffer, this.shadowVertexCount);
            drawLines(this.guidePositionBuffer, this.guideColorBuffer, this.guideVertexCount);
            drawTriangles(this.ribbonPositionBuffer, this.ribbonColorBuffer, this.ribbonVertexCount);
          } catch (error) {
            elevationLayerReady = false;
            elevationLayerError = error && error.message ? error.message : String(error);
            debugMapState();
          }
        },

        onRemove: function(_map, gl) {
          [
            this.ribbonPositionBuffer,
            this.ribbonColorBuffer,
            this.shadowPositionBuffer,
            this.shadowColorBuffer,
            this.guidePositionBuffer,
            this.guideColorBuffer
          ].forEach((buffer) => {
            if (buffer) {
              gl.deleteBuffer(buffer);
            }
          });
          if (this.program) {
            gl.deleteProgram(this.program);
          }
          elevationLayerReady = false;
          ribbonVertexCount = 0;
          guideVertexCount = 0;
          debugMapState();
        }
      };
    }

    function addElevationLayer() {
      if (!map || !maplibregl.MercatorCoordinate || !Array.isArray(flightData.segments) || !flightData.segments.length) {
        return;
      }
      try {
        map.addLayer(makeFlightElevationLayer());
      } catch (error) {
        elevationLayerReady = false;
        elevationLayerError = error && error.message ? error.message : String(error);
        transientStatus("2D map ready; 3D path unavailable: " + elevationLayerError, "error");
      }
      debugMapState();
    }

    function buildFlightGeoJson() {
      const underlayFeatures = [];
      const segmentFeatures = [];
      const extrusionFeatures = [];
      const markerFeatures = [];

      pathParts.forEach((part, index) => {
        const coordinates = coordinatesFromPart(part);
        if (coordinates.length < 2) {
          return;
        }
        underlayFeatures.push({
          type: "Feature",
          properties: { part: index + 1 },
          geometry: { type: "LineString", coordinates }
        });
      });

      flightData.segments.forEach((segment, index) => {
        const left = coordinateFromPoint(segment.left);
        const right = coordinateFromPoint(segment.right);
        if (!left || !right) {
          return;
        }
        segmentFeatures.push({
          type: "Feature",
          properties: {
            color: segment.color,
            index: index + 1,
            startRow: segment.startRow,
            endRow: segment.endRow,
            value: segment.value
          },
          geometry: { type: "LineString", coordinates: [left, right] }
        });
        const extrusionCoordinates = extrusionCoordinatesForSegment(segment.left, segment.right, pathRibbonWidthMeters);
        if (extrusionCoordinates) {
          extrusionFeatures.push({
            type: "Feature",
            properties: {
              color: segment.color,
              index: index + 1,
              height: Math.max(
                altitudeFloorMeters,
                (altitudeRenderMeters(segment.left) + altitudeRenderMeters(segment.right)) / 2
              )
            },
            geometry: { type: "Polygon", coordinates: extrusionCoordinates }
          });
        }
      });

      const startPoint = flightData.points[0];
      const endPoint = flightData.points[flightData.points.length - 1];
      [
        { point: startPoint, label: "Start", role: "start" },
        { point: endPoint, label: "End", role: "end" }
      ].forEach((entry) => {
        const coordinate = coordinateFromPoint(entry.point);
        if (!coordinate) {
          return;
        }
        markerFeatures.push({
          type: "Feature",
          properties: {
            label: entry.label,
            role: entry.role,
            row: entry.point.row,
            altitude: entry.point.alt
          },
          geometry: { type: "Point", coordinates: coordinate }
        });
      });

      return {
        underlay: { type: "FeatureCollection", features: underlayFeatures },
        segments: { type: "FeatureCollection", features: segmentFeatures },
        extrusions: { type: "FeatureCollection", features: extrusionFeatures },
        markers: { type: "FeatureCollection", features: markerFeatures }
      };
    }

    function resizeFlightCanvas() {
      if (!flightCanvas || !flightCanvasContext || !map) {
        return false;
      }
      const canvas = map.getCanvas();
      const width = Math.max(1, canvas.clientWidth || canvas.width || 1);
      const height = Math.max(1, canvas.clientHeight || canvas.height || 1);
      const pixelRatio = window.devicePixelRatio || 1;
      const targetWidth = Math.max(1, Math.floor(width * pixelRatio));
      const targetHeight = Math.max(1, Math.floor(height * pixelRatio));
      if (flightCanvas.width !== targetWidth || flightCanvas.height !== targetHeight) {
        flightCanvas.width = targetWidth;
        flightCanvas.height = targetHeight;
        flightCanvas.style.width = `${width}px`;
        flightCanvas.style.height = `${height}px`;
      }
      flightCanvasContext.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
      return true;
    }

    function altitudePixels(point) {
      if (!map) {
        return 0;
      }
      const zoomScale = Math.pow(2, map.getZoom() - 17);
      const pixelsPerMeter = Math.max(0.12, Math.min(2.0, zoomScale * 0.55));
      const pitchFactor = Math.max(0.25, Math.sin(map.getPitch() * Math.PI / 180));
      return altitudeRenderMeters(point) * pixelsPerMeter * pitchFactor;
    }

    function projectedPoint(point, elevated) {
      const coordinate = coordinateFromPoint(point);
      if (!map || !coordinate) {
        return null;
      }
      const projected = map.project(coordinate);
      return {
        x: projected.x,
        y: projected.y - (elevated ? altitudePixels(point) : 0)
      };
    }

    function drawCanvasLine(points, options) {
      if (!flightCanvasContext || points.length < 2) {
        return false;
      }
      flightCanvasContext.save();
      flightCanvasContext.lineCap = "round";
      flightCanvasContext.lineJoin = "round";
      flightCanvasContext.strokeStyle = options.color;
      flightCanvasContext.globalAlpha = options.alpha == null ? 1 : options.alpha;
      flightCanvasContext.lineWidth = options.width;
      flightCanvasContext.beginPath();
      let started = false;
      points.forEach((point) => {
        if (!point) {
          started = false;
          return;
        }
        if (!started) {
          flightCanvasContext.moveTo(point.x, point.y);
          started = true;
        } else {
          flightCanvasContext.lineTo(point.x, point.y);
        }
      });
      flightCanvasContext.stroke();
      flightCanvasContext.restore();
      return true;
    }

    function drawFlightCanvas() {
      canvasDrawFrame = null;
      if (!flightCanvas || !flightCanvasContext || !map || !resizeFlightCanvas()) {
        canvasPathReady = false;
        debugMapState();
        return false;
      }
      const canvas = map.getCanvas();
      const width = Math.max(1, canvas.clientWidth || canvas.width || 1);
      const height = Math.max(1, canvas.clientHeight || canvas.height || 1);
      flightCanvasContext.clearRect(0, 0, width, height);

      let drawn = false;
      const drawFallbackPath = !extrusionLayerReady && !elevationLayerReady;
      const drawAccentPath = extrusionLayerReady || elevationLayerReady;
      const guideStride = Math.max(1, Math.floor(flightData.points.length / 24));
      flightData.points.forEach((point, index) => {
        if (index !== 0 && index !== flightData.points.length - 1 && index % guideStride !== 0) {
          return;
        }
        const ground = projectedPoint(point, false);
        const elevated = projectedPoint(point, true);
        if (ground && elevated) {
          drawn = drawCanvasLine([ground, elevated], { color: "#ffffff", alpha: 0.28, width: 1.5 }) || drawn;
        }
      });

      if (drawFallbackPath || drawAccentPath) {
        pathParts.forEach((part) => {
          const points = part.map((point) => projectedPoint(point, true));
          drawn = drawCanvasLine(points, {
            color: "#111827",
            alpha: drawFallbackPath ? 0.72 : 0.45,
            width: drawFallbackPath ? 10 : 7
          }) || drawn;
        });

        flightData.segments.forEach((segment) => {
          const left = projectedPoint(segment.left, true);
          const right = projectedPoint(segment.right, true);
          if (!left || !right) {
            return;
          }
          drawn = drawCanvasLine([left, right], {
            color: segment.color || "#55d977",
            alpha: drawFallbackPath ? 0.98 : 0.92,
            width: drawFallbackPath ? 6 : 4
          }) || drawn;
        });
      }

      [
        { point: flightData.points[0], color: "#27ae60", label: "Start" },
        { point: flightData.points[flightData.points.length - 1], color: "#eb5757", label: "End" }
      ].forEach((marker) => {
        const point = projectedPoint(marker.point, true);
        if (!point) {
          return;
        }
        flightCanvasContext.save();
        flightCanvasContext.fillStyle = marker.color;
        flightCanvasContext.strokeStyle = "#ffffff";
        flightCanvasContext.lineWidth = 2;
        flightCanvasContext.beginPath();
        flightCanvasContext.arc(point.x, point.y, 7, 0, Math.PI * 2);
        flightCanvasContext.fill();
        flightCanvasContext.stroke();
        flightCanvasContext.font = "13px Arial, sans-serif";
        flightCanvasContext.textAlign = "center";
        flightCanvasContext.textBaseline = "bottom";
        flightCanvasContext.lineWidth = 4;
        flightCanvasContext.strokeStyle = "#111827";
        flightCanvasContext.fillStyle = "#ffffff";
        flightCanvasContext.strokeText(marker.label, point.x, point.y - 10);
        flightCanvasContext.fillText(marker.label, point.x, point.y - 10);
        flightCanvasContext.restore();
        drawn = true;
      });

      drawn = drawCurrentCursorMarker() || drawn;
      canvasPathReady = drawn && drawFallbackPath;
      debugMapState();
      return drawn;
    }

    function scheduleFlightCanvasDraw() {
      if (canvasDrawFrame !== null) {
        return;
      }
      canvasDrawFrame = window.requestAnimationFrame(drawFlightCanvas);
    }

    function calculateBounds(points) {
      const coordinates = points
        .map((point) => coordinateFromPoint(point))
        .filter(Boolean);
      if (!coordinates.length) {
        return null;
      }
      let west = coordinates[0][0];
      let east = coordinates[0][0];
      let south = coordinates[0][1];
      let north = coordinates[0][1];
      coordinates.forEach((coordinate) => {
        west = Math.min(west, coordinate[0]);
        east = Math.max(east, coordinate[0]);
        south = Math.min(south, coordinate[1]);
        north = Math.max(north, coordinate[1]);
      });
      if (west === east) {
        west -= 0.0005;
        east += 0.0005;
      }
      if (south === north) {
        south -= 0.0005;
        north += 0.0005;
      }
      return [[west, south], [east, north]];
    }

    function calculateBearing(start, end) {
      if (!start || !end) {
        return 0;
      }
      const startLat = Number(start.lat) * Math.PI / 180;
      const endLat = Number(end.lat) * Math.PI / 180;
      const deltaLon = (Number(end.lon) - Number(start.lon)) * Math.PI / 180;
      if (!Number.isFinite(startLat) || !Number.isFinite(endLat) || !Number.isFinite(deltaLon)) {
        return 0;
      }
      const y = Math.sin(deltaLon) * Math.cos(endLat);
      const x = Math.cos(startLat) * Math.sin(endLat)
        - Math.sin(startLat) * Math.cos(endLat) * Math.cos(deltaLon);
      return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
    }

    function addFlightLayers() {
      if (flightLayersAdded) {
        return;
      }
      if (!map || !map.isStyleLoaded()) {
        return;
      }
      const geoJson = buildFlightGeoJson();
      map.addSource("flight-underlay-source", {
        type: "geojson",
        data: geoJson.underlay
      });
      map.addLayer({
        id: "flight-underlay",
        type: "line",
        source: "flight-underlay-source",
        layout: {
          "line-cap": "round",
          "line-join": "round"
        },
        paint: {
          "line-color": "#111827",
          "line-width": 3,
          "line-opacity": 0.16
        }
      });

      map.addSource("flight-segments-source", {
        type: "geojson",
        data: geoJson.segments
      });
      map.addLayer({
        id: "flight-segments",
        type: "line",
        source: "flight-segments-source",
        layout: {
          "line-cap": "round",
          "line-join": "round"
        },
        paint: {
          "line-color": ["get", "color"],
          "line-width": 2,
          "line-opacity": 0.22
        }
      });
      map.addSource("flight-extrusions-source", {
        type: "geojson",
        data: geoJson.extrusions
      });
      extrusionLayerReady = geoJson.extrusions.features.length > 0;
      map.addLayer({
        id: "flight-extrusions",
        type: "fill-extrusion",
        source: "flight-extrusions-source",
        paint: {
          "fill-extrusion-color": ["get", "color"],
          "fill-extrusion-height": ["get", "height"],
          "fill-extrusion-base": 0,
          "fill-extrusion-opacity": 0.9,
          "fill-extrusion-vertical-gradient": true
        }
      });

      map.addSource("flight-markers", {
        type: "geojson",
        data: geoJson.markers
      });
      map.addLayer({
        id: "flight-marker-circles",
        type: "circle",
        source: "flight-markers",
        paint: {
          "circle-radius": 7,
          "circle-color": ["match", ["get", "role"], "start", "#27ae60", "end", "#eb5757", "#9ca3af"],
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 2
        }
      });
      map.addLayer({
        id: "flight-marker-labels",
        type: "symbol",
        source: "flight-markers",
        layout: {
          "text-field": ["get", "label"],
          "text-size": 13,
          "text-offset": [0, -1.6],
          "text-anchor": "bottom",
          "text-allow-overlap": true
        },
        paint: {
          "text-color": "#ffffff",
          "text-halo-color": "#111827",
          "text-halo-width": 2
        }
      });
      addElevationLayer();
      flightLayersAdded = true;
      drawFlightCanvas();
      debugMapState();
    }

    function buildRasterBaseStyle() {
      return {
        version: 8,
        sources: {
          "osm-raster-source": {
            type: "raster",
            tiles: rasterTileUrls,
            tileSize: 256,
            minzoom: 0,
            maxzoom: rasterTileMaxZoom,
            attribution: "&copy; OpenStreetMap contributors"
          }
        },
        layers: [
          {
            id: "background",
            type: "background",
            paint: { "background-color": "__BACKGROUND__" }
          },
          {
            id: "osm-raster-base",
            type: "raster",
            source: "osm-raster-source",
            paint: { "raster-opacity": 1 }
          }
        ]
      };
    }

    function fitFlightBounds() {
      if (!flightBounds) {
        return;
      }
      map.fitBounds(flightBounds, {
        padding: 52,
        maxZoom: mapFitMaxZoom,
        duration: 0
      });
    }

    function setCameraControlState(mode) {
      activeCameraMode = mode || activeCameraMode;
      if (!cameraControls) {
        return;
      }
      Array.from(cameraControls.querySelectorAll("button[data-camera-mode]")).forEach((button) => {
        button.setAttribute("aria-pressed", button.dataset.cameraMode === activeCameraMode ? "true" : "false");
      });
    }

    function setCameraMode(mode, options) {
      if (!map) {
        return false;
      }
      const requestedMode = mode || "orbit";
      const animate = !(options && options.animate === false);
      const fit = !!(options && options.fit);
      const modePitch = requestedMode === "top"
        ? 0
        : (requestedMode === "ground" ? Math.min(84, mapMaxPitch) : Math.min(54, mapMaxPitch));
      const modeBearing = requestedMode === "top" ? 0 : initialBearing;
      const centerPoint = requestedMode === "ground"
        ? coordinateFromPoint(flightData.points[0])
        : null;
      const cameraOptions = { duration: animate ? 650 : 0 };
      if (fit && flightBounds && typeof map.cameraForBounds === "function") {
        const fitted = map.cameraForBounds(flightBounds, {
          padding: { top: 86, right: 72, bottom: 132, left: 72 },
          maxZoom: mapFitMaxZoom,
          bearing: modeBearing,
          pitch: modePitch
        });
        if (fitted) {
          if (fitted.center) {
            cameraOptions.center = fitted.center;
          }
          if (Number.isFinite(Number(fitted.zoom))) {
            cameraOptions.zoom = Math.min(Number(fitted.zoom), requestedMode === "top" ? mapFitMaxZoom : mapMaxZoom);
          }
        }
      } else if (fit) {
        fitFlightBounds();
      }
      if (requestedMode === "top") {
        Object.assign(cameraOptions, {
          pitch: modePitch,
          bearing: modeBearing,
          zoom: Number.isFinite(Number(cameraOptions.zoom)) ? cameraOptions.zoom : Math.min(mapFitMaxZoom, mapMaxZoom)
        });
      } else if (requestedMode === "ground") {
        Object.assign(cameraOptions, {
          pitch: modePitch,
          bearing: modeBearing,
          zoom: Math.min(Math.max(map.getZoom(), 18.2), mapMaxZoom)
        });
        if (centerPoint) {
          cameraOptions.center = centerPoint;
        }
      } else {
        Object.assign(cameraOptions, {
          pitch: modePitch,
          bearing: modeBearing,
          zoom: Number.isFinite(Number(cameraOptions.zoom))
            ? cameraOptions.zoom
            : Math.min(Math.max(map.getZoom(), 15.4), mapFitMaxZoom)
        });
      }
      map.easeTo(cameraOptions);
      setCameraControlState(requestedMode);
      refreshMapViewport({ fit: false });
      return true;
    }

    function refreshMapViewport(options) {
      if (!map) {
        return false;
      }
      map.resize();
      if (options && options.fit) {
        fitFlightBounds();
      }
      scheduleFlightCanvasDraw();
      if (typeof map.triggerRepaint === "function") {
        map.triggerRepaint();
      }
      debugMapState();
      if (viewportRefreshFrame !== null) {
        return true;
      }
      viewportRefreshFrame = window.requestAnimationFrame(() => {
        viewportRefreshFrame = null;
        map.resize();
        scheduleFlightCanvasDraw();
        if (typeof map.triggerRepaint === "function") {
          map.triggerRepaint();
        }
      });
      return true;
    }

    function bindPlaybackControls() {
      updatePlaybackControls();
      if (!timelineEnabled) {
        return;
      }
      if (playPauseButton) {
        playPauseButton.addEventListener("click", () => {
          callGpsBridge("setPlaying", !currentCursor.playing);
        });
      }
      if (playbackSpeed) {
        playbackSpeed.addEventListener("change", () => {
          callGpsBridge("setSpeed", Number(playbackSpeed.value) || 1);
        });
      }
      if (playbackSlider) {
        playbackSlider.addEventListener("input", () => {
          const duration = Math.max(0, Number(currentCursor.durationSeconds || timelineDurationSeconds) || 0);
          const elapsed = duration > 0 ? (Number(playbackSlider.value) / 1000) * duration : 0;
          setCursor({ elapsedSeconds: elapsed, durationSeconds: duration, values: currentCursor.values });
          callGpsBridge("seekElapsed", elapsed);
        });
      }
    }

    function initMapLibreFlightMap() {
      if (!window.maplibregl) {
        throw new Error("MapLibre library failed to load");
      }
      connectGpsBridge();
      bindPlaybackControls();
      const startPoint = flightData.points[0];
      const endPoint = flightData.points[flightData.points.length - 1];
      initialBearing = calculateBearing(startPoint, endPoint);
      flightBounds = calculateBounds(flightData.points);
      const center = coordinateFromPoint(startPoint) || [-75, 39];
      map = new maplibregl.Map({
        container: "map",
        style: buildRasterBaseStyle(),
        center,
        zoom: 13,
        maxZoom: mapMaxZoom,
        minPitch: 0,
        maxPitch: mapMaxPitch,
        pitch: Math.min(54, mapMaxPitch),
        bearing: initialBearing,
        attributionControl: false,
        trackResize: true
      });
      window.__sloppyDebugMap = map;
      debugMapState();
      map.dragRotate.enable();
      map.touchZoomRotate.enableRotation();
      map.addControl(new maplibregl.NavigationControl({
        visualizePitch: true
      }), "top-right");
      map.addControl(new maplibregl.AttributionControl({
        compact: true,
        customAttribution: '<a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>'
      }), "bottom-right");
      if (cameraControls) {
        cameraControls.addEventListener("click", (event) => {
          const button = event.target && event.target.closest
            ? event.target.closest("button[data-camera-mode]")
            : null;
          if (button) {
            setCameraMode(button.dataset.cameraMode, { fit: button.dataset.cameraMode !== "ground" });
          }
        });
      }
      ["move", "zoom", "rotate", "pitch", "resize"].forEach((eventName) => {
        map.on(eventName, scheduleFlightCanvasDraw);
      });

      map.on("error", (event) => {
        const details = event && event.error && (event.error.message || String(event.error));
        setStatus("Map render error" + (details ? ": " + details : ""), "error");
      });

      const revealFlightPath = () => {
        try {
          addFlightLayers();
          renderLegend();
          setCursor(currentCursor);
          fitFlightBounds();
          setCameraMode("orbit", { animate: false, fit: false });
          refreshMapViewport();
          transientStatus("Map ready", "ok", 1200);
        } catch (error) {
          const details = error && error.message ? error.message : String(error);
          setStatus("Map render error: " + details, "error");
        }
      };
      map.once("style.load", revealFlightPath);
      map.once("load", revealFlightPath);
      refreshMapViewport({ fit: true });
      window.setTimeout(() => refreshMapViewport({ fit: true }), 250);
      window.setTimeout(() => refreshMapViewport({ fit: true }), 1000);
    }

    window.sloppyGpsMap = {
      fit: fitFlightBounds,
      refresh: refreshMapViewport,
      setCameraMode,
      setCursor,
      setPlayback,
      getState: debugMapState
    };

    try {
      transientStatus("Preparing map...", "info", 900);
      initMapLibreFlightMap();
    } catch (error) {
      const details = error && error.message ? error.message : String(error);
      setStatus("Map render error: " + details, "error");
    }
  </script>
</body>
</html>
"""
    replacements = {
        "__MAPLIBRE_CSS_URI__": css_uri,
        "__MAPLIBRE_JS_URI__": js_uri,
        "__MAPLIBRE_WORKER_URI__": worker_uri_json,
        "__FLIGHT_DATA__": data_json,
        "__BACKGROUND__": background,
        "__PANEL_BG__": panel_bg,
        "__PANEL_FG__": panel_fg,
        "__BORDER__": border,
    }
    # Replace placeholder tokens last so the template stays readable above and
    # the payload can be serialized with strict JSON escaping.
    for token, value in replacements.items():
        document = document.replace(token, value)
    return document
