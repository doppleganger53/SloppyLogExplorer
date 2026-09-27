"""MapLibre HTML renderer for multi-log radio reception cells."""

from __future__ import annotations

import html
import json
import math
import re
from pathlib import Path
from typing import Any

from .map_basemaps import BASEMAP_RUNTIME_JAVASCRIPT, build_basemap_config

MAP_MAX_ZOOM = 22
MAP_FIT_MAX_ZOOM = 17
DEFAULT_CELL_SIZE_METERS = 5.0
WEB_MERCATOR_MAX_LATITUDE = 85.05112878


def _asset_uri(filename: str) -> str:
    """Return a file URI for a MapLibre asset bundled with the application."""
    asset_path = Path(__file__).resolve().parent / "assets" / "maplibre" / filename
    return asset_path.as_uri()


def _message_html(message: str, dark: bool, state: str) -> str:
    background = "#1f242b" if dark else "#ffffff"
    color = "#e5e7eb" if dark else "#1f2937"
    border = "#3a414d" if dark else "#d1d5db"
    state_json = json.dumps(state)
    return f"""
<!doctype html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;background:{background};color:{color};font-family:Arial,sans-serif;">
  <div style="height:100vh;display:flex;align-items:center;justify-content:center;text-align:center;">
    <div role="status" style="border:1px solid {border};border-radius:6px;padding:18px 22px;max-width:520px;">
      {html.escape(message)}
    </div>
  </div>
  <script>
    window.sloppyReceptionMap = {{
      fit: function() {{ return false; }},
      refresh: function() {{ return false; }},
      setOpacity: function() {{ return false; }},
      setColorScale: function() {{ return false; }},
      setBasemap: function() {{ return false; }},
      setImageryOpacity: function() {{ return false; }},
      getState: function() {{
        return {{state: {state_json}, ready: false, cellCount: 0, layers: {{fill: false, outline: false}}}};
      }}
    }};
  </script>
</body>
</html>
"""


def _json_for_script(value: Any) -> str:
    """Serialize strict JSON without allowing a payload to end the script tag."""
    return json.dumps(value, allow_nan=False, separators=(",", ":")).replace("<", "\\u003c")


def build_reception_map_html(payload: dict[str, object] | None, dark: bool = True) -> str:
    """Build a self-contained reception-map document from a JSON-safe payload.

    The canonical cell shape uses ``latitude``, ``longitude``, ``value``,
    ``sample_count``, and ``flight_count``. A precomputed ``polygon`` or
    ``bounds`` may be supplied instead. A few camel-case aliases are accepted
    at the renderer boundary so persisted/internal payloads do not need a UI
    specific conversion step. Nonempty maps require density-focus metadata so
    the renderer never substitutes a global centroid that can land off-data.
    """
    if payload is None:
        return _message_html("Generate a reception map to display observed coverage.", dark, "empty")

    status = str(payload.get("status", "ok"))
    if status != "ok":
        message = str(payload.get("message") or "Reception map generation failed.")
        state = "empty" if status in {"empty", "cancelled", "canceled", "no-data"} else "error"
        return _message_html(message, dark, state)

    cells = payload.get("cells")
    if not isinstance(cells, list) or not cells:
        message = str(payload.get("message") or "No reception samples matched the selected filters.")
        return _message_html(message, dark, "empty")
    raw_focus = payload.get("viewport_focus", payload.get("viewportFocus"))
    if not isinstance(raw_focus, dict):
        return _message_html(
            "Reception map is missing density-focus metadata. Regenerate the heatmap.",
            dark,
            "error",
        )
    raw_focus_latitude = raw_focus.get("latitude", raw_focus.get("lat"))
    raw_focus_longitude = raw_focus.get(
        "longitude",
        raw_focus.get("lon", raw_focus.get("lng")),
    )
    try:
        focus_latitude = (
            float(raw_focus_latitude) if raw_focus_latitude is not None else math.nan
        )
        focus_longitude = (
            float(raw_focus_longitude) if raw_focus_longitude is not None else math.nan
        )
    except (TypeError, ValueError):
        focus_latitude = math.nan
        focus_longitude = math.nan
    if not (
        math.isfinite(focus_latitude)
        and math.isfinite(focus_longitude)
        and abs(focus_latitude) <= 90.0
        and abs(focus_longitude) <= 180.0
    ):
        return _message_html(
            "Reception map density-focus coordinates are invalid. Regenerate the heatmap.",
            dark,
            "error",
        )
    if abs(focus_latitude) >= WEB_MERCATOR_MAX_LATITUDE:
        return _message_html(
            "Reception map data lies at or beyond the Web Mercator display limit of ±85.051° latitude.",
            dark,
            "error",
        )

    background = "#1f242b" if dark else "#ffffff"
    panel_bg = "rgba(21,24,29,0.92)" if dark else "rgba(255,255,255,0.94)"
    panel_fg = "#f3f4f6" if dark else "#111827"
    secondary_fg = "#cbd5e1" if dark else "#4b5563"
    border = "rgba(255,255,255,0.22)" if dark else "rgba(0,0,0,0.20)"
    outline = "rgba(255,255,255,0.36)" if dark else "rgba(17,24,39,0.42)"
    css_uri = html.escape(_asset_uri("maplibre-gl.css"), quote=True)
    js_uri = json.dumps(_asset_uri("maplibre-gl.mjs"))
    worker_uri_json = _json_for_script(_asset_uri("maplibre-gl-worker.mjs"))
    data_json = _json_for_script(payload)
    basemap_config_json = _json_for_script(build_basemap_config(payload))

    document = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="__MAPLIBRE_CSS_URI__">
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
    #map { position: absolute; inset: 0; width: 100%; height: 100%; }
    #status {
      position: absolute;
      left: 50%;
      top: 16px;
      z-index: 5;
      transform: translateX(-50%);
      max-width: min(560px, calc(100vw - 32px));
      border: 1px solid __BORDER__;
      border-radius: 6px;
      padding: 8px 12px;
      background: __PANEL_BG__;
      color: __PANEL_FG__;
      box-shadow: 0 2px 10px rgba(0,0,0,0.28);
      font-size: 13px;
      pointer-events: none;
    }
    #status[hidden] { display: none; }
    #legend {
      position: absolute;
      left: 12px;
      bottom: 28px;
      z-index: 4;
      width: min(290px, calc(100vw - 52px));
      border: 1px solid __BORDER__;
      border-radius: 7px;
      padding: 10px 12px;
      background: __PANEL_BG__;
      color: __PANEL_FG__;
      box-shadow: 0 2px 10px rgba(0,0,0,0.25);
      font-size: 12px;
    }
    .legend-title {
      overflow: hidden;
      margin-bottom: 7px;
      font-weight: 600;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .legend-gradient {
      height: 12px;
      border: 1px solid __BORDER__;
      border-radius: 3px;
      background: linear-gradient(90deg, #dc2626 0%, #facc15 50%, #16a34a 100%);
    }
    .legend-gradient.reverse {
      background: linear-gradient(90deg, #16a34a 0%, #facc15 50%, #dc2626 100%);
    }
    .legend-range { display: flex; justify-content: space-between; margin-top: 4px; }
    .legend-detail { margin-top: 5px; color: __SECONDARY_FG__; line-height: 1.35; }
    .maplibregl-popup-content {
      border: 1px solid __BORDER__;
      border-radius: 6px;
      padding: 8px 10px;
      background: __PANEL_BG__;
      color: __PANEL_FG__;
      box-shadow: 0 2px 10px rgba(0,0,0,0.3);
      font: 12px/1.4 Arial, sans-serif;
    }
    .maplibregl-popup-tip { display: none; }
    .hover-title { margin-bottom: 3px; font-weight: 600; }
    .hover-row { white-space: nowrap; }
    #basemap-attribution {
      position: absolute;
      right: 8px;
      bottom: 2px;
      z-index: 10;
      border-radius: 3px;
      padding: 2px 4px;
      background: rgba(255,255,255,0.84);
      color: #111827;
      font-size: 11px;
    }
    #basemap-attribution a { color: #0645ad; }
  </style>
</head>
<body>
  <div id="map" aria-label="Radio reception map"></div>
  <div id="status" role="status">Preparing reception map...</div>
  <section id="legend" aria-label="Reception map legend">
    <div id="legend-title" class="legend-title">Radio reception</div>
    <div id="legend-gradient" class="legend-gradient"></div>
    <div class="legend-range"><span id="legend-min"></span><span id="legend-max"></span></div>
    <div id="legend-detail" class="legend-detail"></div>
  </section>
  <div id="basemap-attribution" aria-label="Active basemap attribution"></div>
  <script type="module">
    import * as maplibregl from __MAPLIBRE_JS_URI__;
    window.maplibregl = maplibregl;
    maplibregl.setWorkerUrl(__MAPLIBRE_WORKER_URI__);
    const receptionData = __RECEPTION_DATA__;
    const basemapConfig = __BASEMAP_CONFIG__;
    const mapMaxZoom = 22;
    const fitMaxZoom = 17;
    const defaultCellSizeMeters = 5;
    const viewportMaxRadiusMeters = 2000;
    const earthRadiusMeters = 6371008.8;
    const mercatorMaxLatitude = 85.05112878;
    const mapTileSize = 512;
    const statusElement = document.getElementById("status");
    const legendTitle = document.getElementById("legend-title");
    const legendGradient = document.getElementById("legend-gradient");
    const legendMin = document.getElementById("legend-min");
    const legendMax = document.getElementById("legend-max");
    const legendDetail = document.getElementById("legend-detail");
    const basemapAttributionElement = document.getElementById("basemap-attribution");
    let map = null;
__BASEMAP_RUNTIME__
    window.handleBasemapError = handleBasemapError;
    let mapState = "loading";
    let mapError = null;
    let layersAdded = false;
    let popup = null;
    let featureCollection = {type: "FeatureCollection", features: []};
    let dataBounds = null;
    let focusBounds = null;
    let focusFeatures = [];
    let viewportFocus = null;

    function finiteNumber(value) {
      if (value === null || value === undefined) return null;
      if (typeof value === "string" && value.trim() === "") return null;
      const result = Number(value);
      return Number.isFinite(result) ? result : null;
    }

    function firstValue(object, keys, fallback) {
      for (const key of keys) {
        if (object && object[key] !== undefined && object[key] !== null) {
          return object[key];
        }
      }
      return fallback;
    }

    function configuredCellSizeMeters() {
      return Math.max(0.1, finiteNumber(firstValue(receptionData, ["cell_size_m", "cellSizeMeters"], null))
        || defaultCellSizeMeters);
    }

    function clamp(value, minimum, maximum) {
      return Math.max(minimum, Math.min(maximum, value));
    }

    let heatmapOpacity = clamp(
      finiteNumber(firstValue(receptionData, ["opacity", "heatmap_opacity", "heatmapOpacity"], 0.72)) ?? 0.72,
      0,
      1
    );

    function outlineOpacity() {
      return clamp(heatmapOpacity * 1.25, 0, 1);
    }

    function wrapLongitude(longitude) {
      const numeric = Number(longitude);
      const wrapped = ((numeric + 180) % 360 + 360) % 360 - 180;
      return wrapped === -180 && numeric > 0 ? 180 : wrapped;
    }

    function longitudeNear(longitude, reference) {
      const delta = ((Number(longitude) - Number(reference) + 180) % 360 + 360) % 360 - 180;
      return Number(reference) + delta;
    }

    function haversineMeters(latitudeA, longitudeA, latitudeB, longitudeB) {
      const latitude1 = Number(latitudeA) * Math.PI / 180;
      const latitude2 = Number(latitudeB) * Math.PI / 180;
      const latitudeDelta = latitude2 - latitude1;
      const longitudeDelta = longitudeNear(longitudeB, longitudeA) - Number(longitudeA);
      const longitudeRadians = longitudeDelta * Math.PI / 180;
      const haversine = Math.sin(latitudeDelta / 2) ** 2
        + Math.cos(latitude1) * Math.cos(latitude2) * Math.sin(longitudeRadians / 2) ** 2;
      return 2 * earthRadiusMeters * Math.asin(Math.min(1, Math.sqrt(Math.max(0, haversine))));
    }

    function ringCenter(ring) {
      if (!Array.isArray(ring) || !ring.length) return null;
      const points = ring.length > 1
        && ring[0][0] === ring[ring.length - 1][0]
        && ring[0][1] === ring[ring.length - 1][1]
        ? ring.slice(0, -1)
        : ring;
      if (!points.length) return null;
      const reference = points[0][0];
      const longitude = points.reduce((total, point) => total + longitudeNear(point[0], reference), 0) / points.length;
      const latitude = points.reduce((total, point) => total + point[1], 0) / points.length;
      return [wrapLongitude(longitude), latitude];
    }

    function closeRing(coordinates) {
      if (!Array.isArray(coordinates)) return null;
      const ring = coordinates
        .map((point) => Array.isArray(point) && point.length >= 2
          ? [finiteNumber(point[0]), finiteNumber(point[1])]
          : null)
        .filter((point) => point && point[0] !== null && point[1] !== null);
      if (ring.length < 3) return null;
      const first = ring[0];
      const last = ring[ring.length - 1];
      if (first[0] !== last[0] || first[1] !== last[1]) ring.push(first.slice());
      return ring;
    }

    function boundsRing(bounds) {
      if (!Array.isArray(bounds)) return null;
      let west, south, east, north;
      if (bounds.length >= 4 && !Array.isArray(bounds[0])) {
        [west, south, east, north] = bounds.map(finiteNumber);
      } else if (bounds.length >= 2 && Array.isArray(bounds[0]) && Array.isArray(bounds[1])) {
        west = finiteNumber(bounds[0][0]);
        south = finiteNumber(bounds[0][1]);
        east = finiteNumber(bounds[1][0]);
        north = finiteNumber(bounds[1][1]);
      }
      if ([west, south, east, north].some((value) => value === null || value === undefined)) return null;
      return closeRing([[west, south], [east, south], [east, north], [west, north]]);
    }

    function centerRing(cell) {
      const latitude = finiteNumber(firstValue(cell, ["latitude", "lat", "center_latitude", "centerLat"], null));
      const longitude = finiteNumber(firstValue(cell, ["longitude", "lon", "lng", "center_longitude", "centerLon"], null));
      if (latitude === null || longitude === null || Math.abs(latitude) > 90 || Math.abs(longitude) > 180) return null;
      const sizeMeters = Math.max(0.1, finiteNumber(firstValue(cell, ["cell_size_m", "cellSizeMeters", "size_m"], null))
        || configuredCellSizeMeters());
      const halfLatitude = (sizeMeters / 2) / 111320;
      const longitudeScale = Math.max(0.01, Math.cos(latitude * Math.PI / 180));
      const halfLongitude = (sizeMeters / 2) / (111320 * longitudeScale);
      return closeRing([
        [longitude - halfLongitude, latitude - halfLatitude],
        [longitude + halfLongitude, latitude - halfLatitude],
        [longitude + halfLongitude, latitude + halfLatitude],
        [longitude - halfLongitude, latitude + halfLatitude]
      ]);
    }

    function cellRing(cell) {
      let candidate = firstValue(cell, ["polygon", "coordinates", "corners"], null);
      if (Array.isArray(candidate) && candidate.length === 1 && Array.isArray(candidate[0])) candidate = candidate[0];
      return closeRing(candidate)
        || boundsRing(firstValue(cell, ["bounds", "bbox"], null))
        || centerRing(cell);
    }

    function cellFeature(cell, index) {
      const ring = cellRing(cell);
      const value = finiteNumber(firstValue(cell, ["value", "median", "telemetry_value", "telemetryValue"], null));
      if (!ring || value === null) return null;
      const derivedCenter = ringCenter(ring);
      const rawLatitude = finiteNumber(firstValue(cell, ["latitude", "lat", "center_latitude", "centerLat"], null));
      const rawLongitude = finiteNumber(firstValue(cell, ["longitude", "lon", "lng", "center_longitude", "centerLon"], null));
      const centerLatitude = rawLatitude !== null ? rawLatitude : derivedCenter && derivedCenter[1];
      const centerLongitude = rawLongitude !== null ? rawLongitude : derivedCenter && derivedCenter[0];
      return {
        type: "Feature",
        geometry: {type: "Polygon", coordinates: [ring]},
        properties: {
          value,
          centerLatitude,
          centerLongitude,
          sampleCount: Math.max(0, Math.trunc(finiteNumber(firstValue(cell, ["sample_count", "sampleCount", "samples"], 0)) || 0)),
          flightCount: Math.max(0, Math.trunc(finiteNumber(firstValue(cell, ["flight_count", "flightCount", "log_count", "logCount", "logs"], 0)) || 0))
        }
      };
    }

    function buildFeatures() {
      const cells = Array.isArray(receptionData.cells) ? receptionData.cells : [];
      featureCollection = {
        type: "FeatureCollection",
        features: cells.map(cellFeature).filter(Boolean)
      };
      return featureCollection;
    }

    function featureCenter(feature) {
      const properties = feature && feature.properties ? feature.properties : {};
      const latitude = finiteNumber(properties.centerLatitude);
      const longitude = finiteNumber(properties.centerLongitude);
      if (latitude === null || longitude === null || Math.abs(latitude) > 90) return null;
      return [wrapLongitude(longitude), latitude];
    }

    function resolveViewportFocus() {
      const configured = firstValue(
        receptionData,
        ["viewport_focus", "viewportFocus", "map_focus", "mapFocus"],
        null
      );
      if (configured && typeof configured === "object") {
        const latitude = finiteNumber(firstValue(configured, ["latitude", "lat"], null));
        const longitude = finiteNumber(firstValue(configured, ["longitude", "lon", "lng"], null));
        if (latitude !== null && longitude !== null && Math.abs(latitude) <= 90) {
          const requestedRadius = finiteNumber(firstValue(configured, ["radius_m", "radiusMeters"], viewportMaxRadiusMeters));
          return {
            latitude,
            longitude: wrapLongitude(longitude),
            radiusMeters: Math.min(viewportMaxRadiusMeters, Math.max(1, requestedRadius || viewportMaxRadiusMeters)),
            cellCount: Math.max(0, Math.trunc(finiteNumber(firstValue(configured, ["cell_count", "cellCount"], 0)) || 0)),
            sampleCount: Math.max(0, Math.trunc(finiteNumber(firstValue(configured, ["sample_count", "sampleCount"], 0)) || 0)),
            flightCount: Math.max(0, Math.trunc(finiteNumber(firstValue(configured, ["flight_count", "flightCount"], 0)) || 0))
          };
        }
      }
      return null;
    }

    function selectFocusFeatures(focus) {
      if (!focus) return featureCollection.features.slice();
      const selected = featureCollection.features.filter((feature) => {
        const center = featureCenter(feature);
        return center && haversineMeters(
          focus.latitude,
          focus.longitude,
          center[1],
          center[0]
        ) <= focus.radiusMeters + configuredCellSizeMeters();
      });
      if (selected.length) return selected;
      const nearest = featureCollection.features
        .map((feature) => ({feature, center: featureCenter(feature)}))
        .filter((candidate) => candidate.center !== null)
        .sort((left, right) => (
          haversineMeters(focus.latitude, focus.longitude, left.center[1], left.center[0])
          - haversineMeters(focus.latitude, focus.longitude, right.center[1], right.center[0])
        ));
      return nearest.length ? [nearest[0].feature] : [];
    }

    function valueRange() {
      const values = featureCollection.features.map((feature) => feature.properties.value).filter(Number.isFinite);
      let automaticMin = 0;
      let automaticMax = 1;
      if (values.length) {
        automaticMin = values[0];
        automaticMax = values[0];
        for (let index = 1; index < values.length; index += 1) {
          automaticMin = Math.min(automaticMin, values[index]);
          automaticMax = Math.max(automaticMax, values[index]);
        }
      }
      const rangeObject = receptionData.range && typeof receptionData.range === "object" ? receptionData.range : {};
      const autoRange = firstValue(receptionData, ["auto_range", "autoRange"], true) !== false;
      let minimum = automaticMin;
      let maximum = automaticMax;
      if (!autoRange) {
        minimum = finiteNumber(firstValue(rangeObject, ["min", "minimum"], null));
        if (minimum === null) minimum = finiteNumber(firstValue(receptionData, ["range_min", "rangeMin", "value_min", "valueMin"], null));
        maximum = finiteNumber(firstValue(rangeObject, ["max", "maximum"], null));
        if (maximum === null) maximum = finiteNumber(firstValue(receptionData, ["range_max", "rangeMax", "value_max", "valueMax"], null));
        if (minimum === null) minimum = automaticMin;
        if (maximum === null) maximum = automaticMax;
      }
      if (minimum > maximum) [minimum, maximum] = [maximum, minimum];
      if (minimum === maximum) maximum = minimum + 1;
      return {minimum, maximum, autoRange, reverse: Boolean(firstValue(receptionData, ["reverse", "reverse_colors", "reverseColors"], false))};
    }

    function colorExpression(range) {
      const low = range.reverse ? "#16a34a" : "#dc2626";
      const high = range.reverse ? "#dc2626" : "#16a34a";
      const midpoint = range.minimum + (range.maximum - range.minimum) / 2;
      return ["interpolate", ["linear"], ["get", "value"], range.minimum, low, midpoint, "#facc15", range.maximum, high];
    }

    function formatValue(value) {
      if (!Number.isFinite(Number(value))) return "—";
      return Number(value).toLocaleString(undefined, {maximumFractionDigits: 3});
    }

    function telemetryLabel() {
      return String(firstValue(receptionData, ["telemetry_label", "telemetryLabel", "normalization_label", "normalizationLabel", "telemetry_column", "telemetryColumn", "telemetry_item", "telemetryItem", "channel", "label"], "Radio reception"));
    }

    function telemetryUnit() {
      const unit = firstValue(receptionData, ["unit", "telemetry_unit", "telemetryUnit"], "");
      return unit ? String(unit) : "";
    }

    function renderLegend(range) {
      const label = telemetryLabel();
      const unit = telemetryUnit();
      legendTitle.textContent = label;
      legendGradient.classList.toggle("reverse", range.reverse);
      legendMin.textContent = `${formatValue(range.minimum)}${unit ? ` ${unit}` : ""}`;
      legendMax.textContent = `${formatValue(range.maximum)}${unit ? ` ${unit}` : ""}`;
      const mode = range.autoRange ? "Automatic range" : "Manual range";
      legendDetail.textContent = `${featureCollection.features.length.toLocaleString()} observed ${formatValue(configuredCellSizeMeters())} m cells · ${mode}`;
    }

    function buildRasterBaseStyle() {
      return {
        version: 8,
        sources: basemapConfig.sources,
        layers: [
          {
            id: "background",
            type: "background",
            paint: {"background-color": "__BACKGROUND__"}
          },
          ...basemapRasterLayers()
        ]
      };
    }

    function calculateBounds(features, referenceLongitude) {
      const bounds = new maplibregl.LngLatBounds();
      for (const feature of features) {
        for (const coordinate of feature.geometry.coordinates[0]) {
          bounds.extend([longitudeNear(coordinate[0], referenceLongitude), coordinate[1]]);
        }
      }
      return bounds.isEmpty() ? null : bounds;
    }

    function mercatorY(latitude) {
      const radians = clamp(Number(latitude), -mercatorMaxLatitude, mercatorMaxLatitude) * Math.PI / 180;
      return (1 - Math.log(Math.tan(radians) + (1 / Math.cos(radians))) / Math.PI) / 2;
    }

    function mercatorLatitude(value) {
      return Math.atan(Math.sinh(Math.PI * (1 - 2 * Number(value)))) * 180 / Math.PI;
    }

    function viewportDimensions() {
      const container = map && map.getContainer ? map.getContainer() : null;
      const canvas = map && map.getCanvas ? map.getCanvas() : null;
      return {
        width: Math.max(1, Number(container && container.clientWidth) || Number(canvas && canvas.clientWidth) || 1),
        height: Math.max(1, Number(container && container.clientHeight) || Number(canvas && canvas.clientHeight) || 1)
      };
    }

    function focusFitZoom(center, features, padding) {
      const dimensions = viewportDimensions();
      const availableX = Math.max(1, dimensions.width / 2 - padding);
      const availableY = Math.max(1, dimensions.height / 2 - padding);
      const centerMercatorY = mercatorY(center[1]);
      let maximumX = 0;
      let maximumY = 0;
      for (const feature of features) {
        for (const coordinate of feature.geometry.coordinates[0]) {
          const longitudeDelta = longitudeNear(coordinate[0], center[0]) - center[0];
          maximumX = Math.max(maximumX, Math.abs(longitudeDelta / 360));
          maximumY = Math.max(maximumY, Math.abs(mercatorY(coordinate[1]) - centerMercatorY));
        }
      }
      const horizontalZoom = maximumX > 0
        ? Math.log2(availableX / (mapTileSize * maximumX))
        : fitMaxZoom;
      const verticalZoom = maximumY > 0
        ? Math.log2(availableY / (mapTileSize * maximumY))
        : fitMaxZoom;
      return clamp(Math.min(horizontalZoom, verticalZoom, fitMaxZoom), 0, mapMaxZoom);
    }

    function viewportCornerRadiusMeters(center, zoom) {
      const dimensions = viewportDimensions();
      const worldSize = mapTileSize * (2 ** Number(zoom));
      const centerX = (Number(center[0]) + 180) / 360;
      const centerY = mercatorY(center[1]);
      let maximum = 0;
      for (const xDirection of [-1, 1]) {
        for (const yDirection of [-1, 1]) {
          const cornerX = centerX + xDirection * dimensions.width / (2 * worldSize);
          const cornerY = clamp(centerY + yDirection * dimensions.height / (2 * worldSize), 0, 1);
          const longitude = cornerX * 360 - 180;
          const latitude = mercatorLatitude(cornerY);
          maximum = Math.max(maximum, haversineMeters(center[1], center[0], latitude, longitude));
        }
      }
      return maximum;
    }

    function minimumZoomForRadius(center, radiusMeters) {
      if (viewportCornerRadiusMeters(center, mapMaxZoom) > radiusMeters) return mapMaxZoom;
      let lower = 0;
      let upper = mapMaxZoom;
      for (let iteration = 0; iteration < 32; iteration += 1) {
        const middle = (lower + upper) / 2;
        if (viewportCornerRadiusMeters(center, middle) > radiusMeters) lower = middle;
        else upper = middle;
      }
      return upper;
    }

    function minimumZoomForMercatorEdge(center) {
      const dimensions = viewportDimensions();
      const centerY = clamp(mercatorY(center[1]), 0, 1);
      const verticalClearance = Math.min(centerY, 1 - centerY);
      if (verticalClearance <= 0) return mapMaxZoom;
      const requiredWorldSize = dimensions.height / (2 * verticalClearance);
      return clamp(Math.log2(requiredWorldSize / mapTileSize), 0, mapMaxZoom);
    }

    function visibleViewportRadiusMeters(focus) {
      if (!map || !focus) return null;
      const focusLatitude = clamp(focus.latitude, -mercatorMaxLatitude, mercatorMaxLatitude);
      const bounds = map.getBounds();
      const west = bounds.getWest();
      const east = bounds.getEast();
      const south = bounds.getSouth();
      const north = bounds.getNorth();
      return Math.max(
        haversineMeters(focusLatitude, focus.longitude, south, west),
        haversineMeters(focusLatitude, focus.longitude, south, east),
        haversineMeters(focusLatitude, focus.longitude, north, west),
        haversineMeters(focusLatitude, focus.longitude, north, east)
      );
    }

    function fitReceptionBounds() {
      if (!map || !viewportFocus) return false;
      const center = [
        wrapLongitude(viewportFocus.longitude),
        clamp(viewportFocus.latitude, -mercatorMaxLatitude, mercatorMaxLatitude)
      ];
      const fittedZoom = focusFitZoom(center, focusFeatures, 50);
      const radiusZoom = minimumZoomForRadius(center, viewportFocus.radiusMeters);
      const edgeZoom = minimumZoomForMercatorEdge(center);
      const jumpToZoom = (zoom) => map.jumpTo({
        center,
        zoom,
        pitch: 0,
        bearing: 0
      });
      let selectedZoom = Math.min(mapMaxZoom, Math.max(fittedZoom, radiusZoom, edgeZoom));
      jumpToZoom(selectedZoom);

      // MapLibre may still constrain a near-edge center after jumpTo(). Check
      // the real camera bounds and tighten the zoom until the visible corners
      // are inside the requested radius.
      if (
        selectedZoom < mapMaxZoom
        && Number(visibleViewportRadiusMeters(viewportFocus)) > viewportFocus.radiusMeters
      ) {
        let lower = selectedZoom;
        let upper = mapMaxZoom;
        jumpToZoom(upper);
        if (Number(visibleViewportRadiusMeters(viewportFocus)) <= viewportFocus.radiusMeters) {
          for (let iteration = 0; iteration < 28; iteration += 1) {
            const middle = (lower + upper) / 2;
            jumpToZoom(middle);
            if (Number(visibleViewportRadiusMeters(viewportFocus)) > viewportFocus.radiusMeters) {
              lower = middle;
            } else {
              upper = middle;
            }
          }
          selectedZoom = upper;
          jumpToZoom(selectedZoom);
        }
      }
      const finalRadius = visibleViewportRadiusMeters(viewportFocus);
      if (!Number.isFinite(finalRadius) || finalRadius > viewportFocus.radiusMeters + 0.5) {
        mapState = "error";
        mapError = "Unable to constrain the reception viewport to its requested radius";
        setStatus(`Reception map error: ${mapError}`, "error");
        return false;
      }
      return true;
    }

    function setStatus(message, kind) {
      statusElement.textContent = message;
      statusElement.dataset.kind = kind || "info";
      statusElement.hidden = !message;
    }

    function tooltipContent(properties) {
      const root = document.createElement("div");
      const title = document.createElement("div");
      title.className = "hover-title";
      title.textContent = telemetryLabel();
      root.appendChild(title);
      const rows = [
        ["Value", `${formatValue(properties.value)}${telemetryUnit() ? ` ${telemetryUnit()}` : ""}`],
        ["Samples", Number(properties.sampleCount || 0).toLocaleString()],
        ["Flights", Number(properties.flightCount || 0).toLocaleString()]
      ];
      for (const [label, value] of rows) {
        const row = document.createElement("div");
        row.className = "hover-row";
        row.textContent = `${label}: ${value}`;
        root.appendChild(row);
      }
      return root;
    }

    function bindHover() {
      popup = new maplibregl.Popup({closeButton: false, closeOnClick: false, offset: 8});
      map.on("mousemove", "reception-cells-fill", (event) => {
        const feature = event.features && event.features[0];
        if (!feature) return;
        map.getCanvas().style.cursor = "pointer";
        popup.setLngLat(event.lngLat).setDOMContent(tooltipContent(feature.properties || {})).addTo(map);
      });
      map.on("mouseleave", "reception-cells-fill", () => {
        map.getCanvas().style.cursor = "";
        popup.remove();
      });
    }

    function addReceptionLayers() {
      if (layersAdded) return true;
      if (!map || !map.isStyleLoaded()) return false;
      const range = valueRange();
      map.addSource("reception-cells", {type: "geojson", data: featureCollection});
      map.addLayer({
        id: "reception-cells-fill",
        type: "fill",
        source: "reception-cells",
        paint: {"fill-color": colorExpression(range), "fill-opacity": heatmapOpacity}
      });
      map.addLayer({
        id: "reception-cells-outline",
        type: "line",
        source: "reception-cells",
        paint: {"line-color": "__OUTLINE__", "line-width": 0.7, "line-opacity": outlineOpacity()}
      });
      layersAdded = true;
      bindHover();
      renderLegend(range);
      return true;
    }

    function setOpacity(value) {
      const numeric = finiteNumber(value);
      if (numeric === null) return false;
      heatmapOpacity = clamp(numeric, 0, 1);
      if (map && map.getLayer("reception-cells-fill")) {
        map.setPaintProperty("reception-cells-fill", "fill-opacity", heatmapOpacity);
      }
      if (map && map.getLayer("reception-cells-outline")) {
        map.setPaintProperty("reception-cells-outline", "line-opacity", outlineOpacity());
      }
      if (map && typeof map.triggerRepaint === "function") map.triggerRepaint();
      return true;
    }

    function setColorScale(options) {
      if (!options || typeof options !== "object") return false;
      const autoRange = options.autoRange !== false;
      const minimum = finiteNumber(options.minimum);
      const maximum = finiteNumber(options.maximum);
      if (!autoRange && (minimum === null || maximum === null)) return false;
      receptionData.auto_range = autoRange;
      receptionData.reverse = Boolean(options.reverse);
      delete receptionData.range;
      if (autoRange) {
        delete receptionData.range_min;
        delete receptionData.range_max;
      } else {
        receptionData.range_min = minimum;
        receptionData.range_max = maximum;
      }
      const range = valueRange();
      if (map && map.getLayer("reception-cells-fill")) {
        map.setPaintProperty("reception-cells-fill", "fill-color", colorExpression(range));
      }
      renderLegend(range);
      if (map && typeof map.triggerRepaint === "function") map.triggerRepaint();
      return true;
    }

    function debugState() {
      const range = valueRange();
      const boundsArray = dataBounds ? [dataBounds.getSouthWest().toArray(), dataBounds.getNorthEast().toArray()] : null;
      const focusBoundsArray = focusBounds ? [focusBounds.getSouthWest().toArray(), focusBounds.getNorthEast().toArray()] : null;
      const visibleBounds = map ? map.getBounds() : null;
      const viewportBoundsArray = visibleBounds
        ? [[visibleBounds.getWest(), visibleBounds.getSouth()], [visibleBounds.getEast(), visibleBounds.getNorth()]]
        : null;
      let renderedCellCount = 0;
      let sourceCellCount = 0;
      if (map && map.getLayer("reception-cells-fill")) {
        try {
          renderedCellCount = map.queryRenderedFeatures({layers: ["reception-cells-fill"]}).length;
          sourceCellCount = map.querySourceFeatures("reception-cells").length;
        } catch (_error) {
          renderedCellCount = 0;
          sourceCellCount = 0;
        }
      }
      return {
        state: mapState,
        ready: mapState === "ready" && layersAdded && renderedCellCount > 0,
        cellCount: featureCollection.features.length,
        renderedCellCount,
        sourceCellCount,
        sourceLoaded: Boolean(map && map.getSource("reception-cells") && map.isSourceLoaded("reception-cells")),
        styleLoaded: Boolean(map && map.isStyleLoaded()),
        layers: {
          source: Boolean(map && map.getSource("reception-cells")),
          fill: Boolean(map && map.getLayer("reception-cells-fill")),
          outline: Boolean(map && map.getLayer("reception-cells-outline"))
        },
        camera: map ? {
          pitch: map.getPitch(),
          bearing: map.getBearing(),
          zoom: map.getZoom(),
          center: map.getCenter().toArray()
        } : null,
        range: {minimum: range.minimum, maximum: range.maximum, auto: range.autoRange, reverse: range.reverse},
        bounds: boundsArray,
        focusBounds: focusBoundsArray,
        viewportBounds: viewportBoundsArray,
        viewportFocus: viewportFocus ? {
          latitude: viewportFocus.latitude,
          longitude: viewportFocus.longitude,
          cameraLatitude: clamp(viewportFocus.latitude, -mercatorMaxLatitude, mercatorMaxLatitude),
          radiusMeters: viewportFocus.radiusMeters,
          cellCount: viewportFocus.cellCount,
          sampleCount: viewportFocus.sampleCount,
          flightCount: viewportFocus.flightCount
        } : null,
        viewportRadiusMeters: visibleViewportRadiusMeters(viewportFocus),
        opacity: heatmapOpacity,
        basemap: basemapDebugState(),
        error: mapError
      };
    }

    function refreshMapViewport(options) {
      if (!map) return false;
      map.resize();
      if (options && options.fit && !fitReceptionBounds()) return false;
      if (typeof map.triggerRepaint === "function") map.triggerRepaint();
      return true;
    }

    function initializeMap() {
      if (!window.maplibregl) throw new Error("MapLibre library failed to load");
      buildFeatures();
      if (!featureCollection.features.length) throw new Error("No valid reception cells to display");
      viewportFocus = resolveViewportFocus();
      if (viewportFocus && Math.abs(viewportFocus.latitude) >= mercatorMaxLatitude) {
        throw new Error("Reception map data lies at or beyond the Web Mercator display limit of ±85.051° latitude");
      }
      focusFeatures = selectFocusFeatures(viewportFocus);
      const referenceLongitude = viewportFocus ? viewportFocus.longitude : 0;
      dataBounds = calculateBounds(featureCollection.features, referenceLongitude);
      focusBounds = calculateBounds(focusFeatures, referenceLongitude);
      const center = viewportFocus
        ? [wrapLongitude(viewportFocus.longitude), clamp(viewportFocus.latitude, -mercatorMaxLatitude, mercatorMaxLatitude)]
        : [-75, 39];
      map = new maplibregl.Map({
        container: "map",
        style: buildRasterBaseStyle(),
        center,
        zoom: 14,
        minZoom: 0,
        maxZoom: mapMaxZoom,
        pitch: 0,
        bearing: 0,
        attributionControl: false,
        trackResize: true
      });
      window.__sloppyReceptionDebugMap = map;
      map.addControl(new maplibregl.NavigationControl({showCompass: false}), "top-right");
      updateBasemapAttribution();
      map.on("moveend", updateBasemapAttribution);
      map.on("style.load", reapplyBasemapState);
      map.on("error", (event) => {
        if (handleBasemapError(event)) return;
        const details = event && event.error && (event.error.message || String(event.error));
        if (details) mapError = details;
      });
      const revealCells = () => {
        if (layersAdded) return;
        try {
          if (!addReceptionLayers()) return;
          if (!fitReceptionBounds()) return;
          mapState = "ready";
          setStatus("", "ok");
          refreshMapViewport({fit: false});
        } catch (error) {
          mapState = "error";
          mapError = error && error.message ? error.message : String(error);
          setStatus(`Reception map error: ${mapError}`, "error");
        }
      };
      map.once("style.load", revealCells);
      map.once("load", revealCells);
      map.once("idle", () => refreshMapViewport({fit: false}));
      window.setTimeout(() => refreshMapViewport({fit: false}), 250);
      window.setTimeout(() => refreshMapViewport({fit: false}), 900);
    }

    window.sloppyReceptionMap = {
      fit: fitReceptionBounds,
      refresh: refreshMapViewport,
      setOpacity,
      setColorScale,
      setBasemap,
      setImageryOpacity,
      getState: debugState
    };

    try {
      initializeMap();
    } catch (error) {
      mapState = "error";
      mapError = error && error.message ? error.message : String(error);
      setStatus(`Reception map error: ${mapError}`, "error");
    }
  </script>
</body>
</html>
"""
    replacements = {
        "__MAPLIBRE_CSS_URI__": css_uri,
        "__MAPLIBRE_JS_URI__": js_uri,
        "__MAPLIBRE_WORKER_URI__": worker_uri_json,
        "__RECEPTION_DATA__": data_json,
        "__BASEMAP_CONFIG__": basemap_config_json,
        "__BASEMAP_RUNTIME__": BASEMAP_RUNTIME_JAVASCRIPT,
        "__BACKGROUND__": background,
        "__PANEL_BG__": panel_bg,
        "__PANEL_FG__": panel_fg,
        "__SECONDARY_FG__": secondary_fg,
        "__BORDER__": border,
        "__OUTLINE__": outline,
    }
    # Never substitute token-like telemetry labels after inserting their JSON.
    return re.sub(r"__[A-Z_]+__", lambda match: replacements.get(match[0], match[0]), document)
