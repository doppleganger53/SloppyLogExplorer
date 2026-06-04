from __future__ import annotations

import html
import json
from pathlib import Path

MAPLIBRE_VERSION = "5.24.0"
OPENFREEMAP_STYLE_URL = "https://tiles.openfreemap.org/styles/liberty"


def normalize_map_mode(mode: str | None) -> str:
    return "2d" if str(mode or "").lower() == "2d" else "3d"


def _asset_uri(filename: str) -> str:
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


def build_gps_map_html(payload: dict[str, object], dark: bool = True, mode: str = "3d") -> str:
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
    mode_json = json.dumps(normalize_map_mode(mode))

    document = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="__MAPLIBRE_CSS_URI__">
  <script src="__MAPLIBRE_JS_URI__"></script>
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
    #statusOverlay,
    #mapModeBadge {
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
    }
    #statusOverlay {
      left: 8px;
      top: 8px;
      min-width: 160px;
      max-width: 360px;
    }
    #mapModeBadge {
      right: 48px;
      top: 8px;
      font-weight: 700;
      letter-spacing: 0;
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
  </style>
</head>
<body>
  <div id="map"></div>
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
  <div id="mapModeBadge"></div>
  <div id="mapAttribution">
    Map style by <a href="https://openfreemap.org/">OpenFreeMap</a> |
    data &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>
  </div>
  <script>
    maplibregl.workerUrl = __MAPLIBRE_WORKER_URI__;
    const flightData = __FLIGHT_DATA__;
    const initialMapMode = __MAP_MODE__;
    const openFreeMapStyleUrl = "__OPENFREEMAP_STYLE_URL__";
    const pathParts = Array.isArray(flightData.pathParts) && flightData.pathParts.length
      ? flightData.pathParts
      : [flightData.points];
    const legendContainer = document.getElementById("legend");
    const legendTitle = document.getElementById("legendTitle");
    const gradientBar = document.getElementById("gradientBar");
    const legendMin = document.getElementById("legendMin");
    const legendMid = document.getElementById("legendMid");
    const legendMax = document.getElementById("legendMax");
    const statusOverlay = document.getElementById("statusOverlay");
    const mapModeBadge = document.getElementById("mapModeBadge");
    let statusHideTimer = null;
    let map = null;
    let activeMapMode = initialMapMode;
    let flightBounds = null;
    let initialBearing = 0;
    let flightLayersAdded = false;

    function finiteNumber(value) {
      return Number.isFinite(Number(value));
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
        return;
      }
      statusOverlay.hidden = false;
      statusOverlay.dataset.kind = kind || "info";
      statusOverlay.textContent = message;
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

    function buildFlightGeoJson() {
      const underlayFeatures = [];
      const segmentFeatures = [];
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
        markers: { type: "FeatureCollection", features: markerFeatures }
      };
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
          "line-color": "#ffffff",
          "line-width": 10,
          "line-opacity": 0.82
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
          "line-width": 6,
          "line-opacity": 0.96
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
      flightLayersAdded = true;
    }

    function fitFlightBounds() {
      if (!flightBounds) {
        return;
      }
      map.fitBounds(flightBounds, {
        padding: 52,
        maxZoom: 17,
        duration: 0
      });
    }

    function applyMapMode(mode, options) {
      activeMapMode = mode === "2d" ? "2d" : "3d";
      if (mapModeBadge) {
        mapModeBadge.textContent = activeMapMode === "3d" ? "3D" : "2D";
      }
      if (!map) {
        return activeMapMode;
      }
      const duration = options && Number.isFinite(options.duration) ? options.duration : 450;
      if (activeMapMode === "3d") {
        map.dragRotate.enable();
        map.touchZoomRotate.enableRotation();
        map.easeTo({
          pitch: 60,
          bearing: initialBearing,
          duration
        });
      } else {
        map.dragRotate.disable();
        map.touchZoomRotate.disableRotation();
        map.easeTo({
          pitch: 0,
          bearing: 0,
          duration
        });
      }
      return activeMapMode;
    }

    function initMapLibreFlightMap() {
      if (!window.maplibregl) {
        throw new Error("MapLibre library failed to load");
      }
      const startPoint = flightData.points[0];
      const endPoint = flightData.points[flightData.points.length - 1];
      initialBearing = calculateBearing(startPoint, endPoint);
      flightBounds = calculateBounds(flightData.points);
      const center = coordinateFromPoint(startPoint) || [-75, 39];
      map = new maplibregl.Map({
        container: "map",
        style: flightData.mapStyleUrl || openFreeMapStyleUrl,
        center,
        zoom: 13,
        maxZoom: 17,
        pitch: activeMapMode === "3d" ? 60 : 0,
        bearing: activeMapMode === "3d" ? initialBearing : 0,
        attributionControl: false
      });
      map.addControl(new maplibregl.NavigationControl({
        visualizePitch: true
      }), "top-right");
      map.addControl(new maplibregl.AttributionControl({
        compact: true,
        customAttribution: '<a href="https://openfreemap.org/">OpenFreeMap</a>'
      }), "bottom-right");

      map.on("error", (event) => {
        const details = event && event.error && (event.error.message || String(event.error));
        setStatus("Map render error" + (details ? ": " + details : ""), "error");
      });

      const revealFlightPath = () => {
        addFlightLayers();
        renderLegend();
        fitFlightBounds();
        applyMapMode(activeMapMode, { duration: 0 });
        transientStatus("Map ready", "ok", 1200);
      };
      map.once("style.load", revealFlightPath);
      map.once("load", revealFlightPath);
      applyMapMode(activeMapMode, { duration: 0 });
    }

    window.sloppyGpsMap = {
      setMode: applyMapMode,
      mode: () => activeMapMode,
      fit: fitFlightBounds
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
        "__MAP_MODE__": mode_json,
        "__OPENFREEMAP_STYLE_URL__": OPENFREEMAP_STYLE_URL,
        "__BACKGROUND__": background,
        "__PANEL_BG__": panel_bg,
        "__PANEL_FG__": panel_fg,
        "__BORDER__": border,
    }
    for token, value in replacements.items():
        document = document.replace(token, value)
    return document
