from __future__ import annotations

import math
from typing import Any, cast

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
