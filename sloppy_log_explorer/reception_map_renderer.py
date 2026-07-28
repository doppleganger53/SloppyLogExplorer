"""MapLibre HTML renderer for multi-log radio reception cells."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


OPENSTREETMAP_RASTER_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
OPENSTREETMAP_RASTER_TILE_MAX_ZOOM = 19
MAP_MAX_ZOOM = 19
MAP_FIT_MAX_ZOOM = 17
DEFAULT_CELL_SIZE_METERS = 5.0


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
    return json.dumps(value, allow_nan=False, separators=(",", ":")).replace("</", "<\\/")


def build_reception_map_html(payload: dict[str, object] | None, dark: bool = True) -> str:
    """Build a self-contained reception-map document from a JSON-safe payload.

    The canonical cell shape uses ``latitude``, ``longitude``, ``value``,
    ``sample_count``, and ``flight_count``. A precomputed ``polygon`` or
    ``bounds`` may be supplied instead. A few camel-case aliases are accepted
    at the renderer boundary so persisted/internal payloads do not need a UI
    specific conversion step.
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

    background = "#1f242b" if dark else "#ffffff"
    panel_bg = "rgba(21,24,29,0.92)" if dark else "rgba(255,255,255,0.94)"
    panel_fg = "#f3f4f6" if dark else "#111827"
    secondary_fg = "#cbd5e1" if dark else "#4b5563"
    border = "rgba(255,255,255,0.22)" if dark else "rgba(0,0,0,0.20)"
    outline = "rgba(255,255,255,0.36)" if dark else "rgba(17,24,39,0.42)"
    css_uri = html.escape(_asset_uri("maplibre-gl.css"), quote=True)
    js_uri = html.escape(_asset_uri("maplibre-gl-csp.js"), quote=True)
    worker_uri_json = _json_for_script(_asset_uri("maplibre-gl-csp-worker.js"))
    data_json = _json_for_script(payload)

    document = r"""
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
  <script>
    const maplibreWorkerUrl = __MAPLIBRE_WORKER_URI__;
    if (typeof maplibregl.setWorkerUrl === "function") {
      maplibregl.setWorkerUrl(maplibreWorkerUrl);
    } else {
      maplibregl.workerUrl = maplibreWorkerUrl;
    }
    const receptionData = __RECEPTION_DATA__;
    const mapMaxZoom = 19;
    const fitMaxZoom = 17;
    const defaultCellSizeMeters = 5;
    const statusElement = document.getElementById("status");
    const legendTitle = document.getElementById("legend-title");
    const legendGradient = document.getElementById("legend-gradient");
    const legendMin = document.getElementById("legend-min");
    const legendMax = document.getElementById("legend-max");
    const legendDetail = document.getElementById("legend-detail");
    let map = null;
    let mapState = "loading";
    let mapError = null;
    let layersAdded = false;
    let popup = null;
    let featureCollection = {type: "FeatureCollection", features: []};
    let dataBounds = null;

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
        || finiteNumber(firstValue(receptionData, ["cell_size_m", "cellSizeMeters"], null))
        || defaultCellSizeMeters);
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
      return {
        type: "Feature",
        geometry: {type: "Polygon", coordinates: [ring]},
        properties: {
          value,
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
      return String(firstValue(receptionData, ["telemetry_column", "telemetryColumn", "telemetry_item", "telemetryItem", "channel", "label"], "Radio reception"));
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
      legendDetail.textContent = `${featureCollection.features.length.toLocaleString()} observed 5 m cells · ${mode}`;
    }

    function buildRasterBaseStyle() {
      return {
        version: 8,
        sources: {
          "osm-raster": {
            type: "raster",
            tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
            tileSize: 256,
            minzoom: 0,
            maxzoom: 19,
            attribution: "&copy; OpenStreetMap contributors"
          }
        },
        layers: [{id: "osm-raster", type: "raster", source: "osm-raster", minzoom: 0, maxzoom: 19}]
      };
    }

    function calculateBounds() {
      const bounds = new maplibregl.LngLatBounds();
      for (const feature of featureCollection.features) {
        for (const coordinate of feature.geometry.coordinates[0]) bounds.extend(coordinate);
      }
      return bounds.isEmpty() ? null : bounds;
    }

    function fitReceptionBounds() {
      if (!map || !dataBounds) return false;
      map.fitBounds(dataBounds, {padding: 50, maxZoom: fitMaxZoom, duration: 0});
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
        paint: {"fill-color": colorExpression(range), "fill-opacity": 0.72}
      });
      map.addLayer({
        id: "reception-cells-outline",
        type: "line",
        source: "reception-cells",
        paint: {"line-color": "__OUTLINE__", "line-width": 0.7, "line-opacity": 0.9}
      });
      layersAdded = true;
      bindHover();
      renderLegend(range);
      return true;
    }

    function debugState() {
      const range = valueRange();
      const boundsArray = dataBounds ? [dataBounds.getSouthWest().toArray(), dataBounds.getNorthEast().toArray()] : null;
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
        error: mapError
      };
    }

    function refreshMapViewport(options) {
      if (!map) return false;
      map.resize();
      if (options && options.fit) fitReceptionBounds();
      if (typeof map.triggerRepaint === "function") map.triggerRepaint();
      return true;
    }

    function initializeMap() {
      if (!window.maplibregl) throw new Error("MapLibre library failed to load");
      buildFeatures();
      if (!featureCollection.features.length) throw new Error("No valid reception cells to display");
      dataBounds = calculateBounds();
      const center = dataBounds ? dataBounds.getCenter().toArray() : [-75, 39];
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
      map.addControl(new maplibregl.AttributionControl({
        compact: true,
        customAttribution: '<a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>'
      }), "bottom-right");
      map.on("error", (event) => {
        const details = event && event.error && (event.error.message || String(event.error));
        if (details) mapError = details;
      });
      const revealCells = () => {
        if (layersAdded) return;
        try {
          if (!addReceptionLayers()) return;
          fitReceptionBounds();
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
      map.once("idle", () => refreshMapViewport({fit: true}));
      window.setTimeout(() => refreshMapViewport({fit: true}), 250);
      window.setTimeout(() => refreshMapViewport({fit: true}), 900);
    }

    window.sloppyReceptionMap = {
      fit: fitReceptionBounds,
      refresh: refreshMapViewport,
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
        "__BACKGROUND__": background,
        "__PANEL_BG__": panel_bg,
        "__PANEL_FG__": panel_fg,
        "__SECONDARY_FG__": secondary_fg,
        "__BORDER__": border,
        "__OUTLINE__": outline,
    }
    for token, value in replacements.items():
        document = document.replace(token, value)
    return document
