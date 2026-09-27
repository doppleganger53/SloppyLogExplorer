from __future__ import annotations

import math
import json
import re
from html.parser import HTMLParser
from typing import Any, cast

import pytest

from sloppy_log_explorer.gps_map_renderer import build_gps_map_html
from sloppy_log_explorer.reception_map_renderer import build_reception_map_html

from sloppy_log_explorer.map_basemaps import (
    BASEMAP_IMAGERY,
    BASEMAP_OPENSTREETMAP,
    BASEMAP_RUNTIME_JAVASCRIPT,
    NASA_GIBS_RASTER_TILE_URL,
    OPENSTREETMAP_RASTER_TILE_URL,
    USGS_NAIP_BOUNDS,
    USGS_NAIP_WMS_TILE_URL,
    build_basemap_config,
    clamp_imagery_opacity,
    normalize_basemap,
)


def test_shared_basemap_config_defaults_to_openstreetmap_with_ordered_fallbacks() -> None:
    config = build_basemap_config()
    sources = cast(dict[str, dict[str, Any]], config["sources"])

    assert config["selected"] == BASEMAP_OPENSTREETMAP
    assert config["imageryOpacity"] == 1.0
    assert list(sources) == [
        "osm-raster-source",
        "nasa-gibs-raster-source",
        "usgs-naip-raster-source",
    ]
    assert sources["osm-raster-source"]["tiles"] == [OPENSTREETMAP_RASTER_TILE_URL]
    assert sources["nasa-gibs-raster-source"]["tiles"] == [NASA_GIBS_RASTER_TILE_URL]
    assert sources["usgs-naip-raster-source"]["tiles"] == [USGS_NAIP_WMS_TILE_URL]
    assert sources["usgs-naip-raster-source"]["bounds"] == list(USGS_NAIP_BOUNDS)

    osm_layer = BASEMAP_RUNTIME_JAVASCRIPT.index('id: "osm-raster-base"')
    gibs_layer = BASEMAP_RUNTIME_JAVASCRIPT.index('id: "nasa-gibs-raster-base"')
    naip_layer = BASEMAP_RUNTIME_JAVASCRIPT.index('id: "usgs-naip-raster-base"')
    assert osm_layer < gibs_layer < naip_layer


def test_imagery_config_uses_transparent_naip_wms_and_global_gibs() -> None:
    config = build_basemap_config({"basemap": "imagery", "imagery_opacity": 0.45})

    assert config["selected"] == BASEMAP_IMAGERY
    assert config["imageryOpacity"] == 0.45
    assert "bbox={bbox-epsg-3857}" in USGS_NAIP_WMS_TILE_URL
    assert "transparent=true" in USGS_NAIP_WMS_TILE_URL
    assert "format=image%2Fpng" in USGS_NAIP_WMS_TILE_URL
    assert "USGSNAIPImagery%3ANaturalColor" in USGS_NAIP_WMS_TILE_URL
    assert "gibs.earthdata.nasa.gov/wmts/epsg3857" in NASA_GIBS_RASTER_TILE_URL
    assert "BlueMarble_ShadedRelief_Bathymetry" in NASA_GIBS_RASTER_TILE_URL

    attribution = cast(dict[str, str], config["attribution"])
    assert "USGS/USDA NAIP" in attribution["naip"]
    assert "NASA GIBS" in attribution["naip"]
    assert "OpenStreetMap contributors" in attribution["naip"]


def test_basemap_and_imagery_opacity_inputs_are_normalized() -> None:
    assert normalize_basemap("IMAGERY") == BASEMAP_IMAGERY
    assert normalize_basemap("unsupported") == BASEMAP_OPENSTREETMAP
    assert clamp_imagery_opacity(-2) == 0.0
    assert clamp_imagery_opacity(2) == 1.0
    assert clamp_imagery_opacity("0.25") == 0.25
    assert clamp_imagery_opacity(math.nan) == 1.0
    assert clamp_imagery_opacity("invalid") == 1.0


def test_style_load_reapply_preserves_provider_failure_state() -> None:
    reapply_block = BASEMAP_RUNTIME_JAVASCRIPT.split(
        "function reapplyBasemapState()", 1
    )[1].split("function setBasemap", 1)[0]

    assert 'activeBasemap === "imagery" && !gibsFailed' in reapply_block
    assert 'activeBasemap === "imagery" && !naipFailed' in reapply_block
    assert '"visibility", gibsVisibility' in reapply_block
    assert '"visibility", naipVisibility' in reapply_block
    assert '"raster-opacity", imageryOpacity' in reapply_block
    assert "naipFailed = false" not in reapply_block
    assert "gibsFailed = false" not in reapply_block


def test_enabled_naip_layer_is_always_included_in_imagery_attribution() -> None:
    assert 'return activeBasemap === "imagery" && !naipFailed;' in BASEMAP_RUNTIME_JAVASCRIPT
    assert (
        'const attributionProvider = naipAttributionRequired() ? "naip" : provider;'
        in BASEMAP_RUNTIME_JAVASCRIPT
    )


@pytest.mark.parametrize("renderer", [build_gps_map_html, build_reception_map_html])
@pytest.mark.parametrize(
    "label",
    [
        "RSSI </script><script>window.injected=true</script>",
        "<!-- <script>unterminated HTML parser escape",
        "__BASEMAP_RUNTIME__ __BACKGROUND__ __SPEED_FG__",
    ],
)
def test_map_payload_remains_data_in_html_script(renderer: Any, label: str) -> None:
    payload = {
        "status": "ok",
        "telemetry_column": label,
        "points": [{"lat": 39.75, "lon": -75.25}],
        "cells": [{"latitude": 39.75, "longitude": -75.25, "value": 90}],
        "viewport_focus": {"latitude": 39.75, "longitude": -75.25},
    }

    class ScriptParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.in_script = False
            self.scripts: list[str] = []

        def handle_starttag(self, tag: str, attrs: Any) -> None:
            self.in_script = tag == "script"

        def handle_endtag(self, tag: str) -> None:
            if tag == "script":
                self.in_script = False

        def handle_data(self, data: str) -> None:
            if self.in_script:
                self.scripts.append(data)

    parser = ScriptParser()
    parser.feed(renderer(payload))
    inline_scripts = [script for script in parser.scripts if script.strip()]
    assert len(inline_scripts) == 1
    match = re.search(r"const (?:flightData|receptionData) = (.*);", inline_scripts[0])
    assert match is not None
    assert json.loads(match[1]) == payload
    assert label not in inline_scripts[0] or "<" not in label


@pytest.mark.parametrize("start_longitude,end_longitude", [(179.999, -179.999), (-179.999, 179.999)])
def test_flight_map_keeps_antimeridian_bounds_playback_and_3d_segments_local(
    start_longitude: float, end_longitude: float
) -> None:
    from PyQt6.QtQml import QJSEngine
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    engine = QJSEngine()
    points = [
        {"lat": -17.5, "lon": start_longitude, "alt": 20, "elapsedSeconds": 0, "row": 1},
        {"lat": -17.5, "lon": end_longitude, "alt": 40, "elapsedSeconds": 2, "row": 2},
    ]
    document = build_gps_map_html({"status": "ok", "points": points})
    names = [
        "finiteNumber", "displayLongitude", "coordinateFromPoint", "allPathPoints",
        "nearestPointForElapsed", "interpolatedPointForElapsed", "calculateBounds",
        "extrusionCoordinatesForSegment", "mercatorCoordinate",
    ]
    definitions = []
    for name in names:
        function = document.split(f"    function {name}(", 1)[1].split("\n    function ", 1)[0]
        definitions.append(f"function {name}(" + function)
    script = (
        f"const pathParts = [{json.dumps(points)}]; const longitudeReference = {start_longitude};"
        "const maplibregl = {MercatorCoordinate:{fromLngLat: (point) => point}};\n"
        + "\n".join(definitions)
        + "\nJSON.stringify({midpoint:interpolatedPointForElapsed(1),"
        "bounds:calculateBounds(pathParts[0]),"
        "polygon:extrusionCoordinatesForSegment(pathParts[0][0],pathParts[0][1],24),"
        "mercator:mercatorCoordinate(pathParts[0][1],40)});"
    )
    evaluated = engine.evaluate(script)
    assert not evaluated.isError(), evaluated.toString()
    result = json.loads(evaluated.toString())
    assert abs(result["midpoint"]["lon"]) == pytest.approx(180)
    assert result["midpoint"]["alt"] == 30
    assert result["bounds"][1][0] - result["bounds"][0][0] == pytest.approx(0.002)
    longitudes = [point[0] for point in result["polygon"][0]]
    assert max(longitudes) - min(longitudes) < 0.003
    assert abs(result["mercator"]["lng"] - start_longitude) == pytest.approx(0.002)
    assert app is not None


def test_late_flight_map_load_does_not_repeat_initial_camera_fit() -> None:
    from PyQt6.QtQml import QJSEngine
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    engine = QJSEngine()
    document = build_gps_map_html({"status": "ok", "points": []})
    callback = document.split("const revealFlightPath = () => {", 1)[1].split("\n      };", 1)[0]
    script = """
      let flightLayersAdded = false;
      let camera = 'initial';
      let fits = 0;
      let currentCursor = {};
      function addFlightLayers() {flightLayersAdded = true;}
      function fitFlightBounds() {fits += 1; camera = 'fitted';}
      function renderLegend() {}
      function setCursor() {}
      function setCameraMode() {}
      function refreshMapViewport() {}
      function transientStatus() {}
      function setStatus(message) {throw new Error(message);}
    """ + "const reveal = () => {" + callback + "};" + """
      reveal();
      camera = 'user';
      reveal();
      JSON.stringify({camera, fits});
    """
    result = engine.evaluate(script)
    assert not result.isError(), result.toString()
    assert json.loads(result.toString()) == {"camera": "user", "fits": 1}
    assert "window.setTimeout(() => refreshMapViewport({ fit: true })" not in document
    assert app is not None
