"""Shared raster basemap configuration for MapLibre map surfaces."""

from __future__ import annotations

import math
from typing import Mapping


BASEMAP_OPENSTREETMAP = "osm"
BASEMAP_IMAGERY = "imagery"
BASEMAP_CHOICES = (
    ("OpenStreetMap", BASEMAP_OPENSTREETMAP),
    ("Imagery (NAIP \N{RIGHTWARDS ARROW} NASA GIBS)", BASEMAP_IMAGERY),
)

DEFAULT_IMAGERY_OPACITY = 1.0

OPENSTREETMAP_RASTER_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
OPENSTREETMAP_RASTER_TILE_MAX_ZOOM = 19

# MapLibre expands ``{bbox-epsg-3857}`` for WMS raster sources. Transparent
# pixels outside NAIP coverage reveal the NASA GIBS layer below it.
USGS_NAIP_WMS_TILE_URL = (
    "https://imagery.nationalmap.gov/arcgis/services/USGSNAIPImagery/"
    "ImageServer/WMSServer?service=WMS&request=GetMap&version=1.1.1"
    "&layers=USGSNAIPImagery%3ANaturalColor&styles=&format=image%2Fpng"
    "&transparent=true&srs=EPSG%3A3857&bbox={bbox-epsg-3857}"
    "&width=256&height=256"
)
USGS_NAIP_RASTER_TILE_MAX_ZOOM = 22
# Current Web Mercator extent published by the USGS NAIP ImageServer.
USGS_NAIP_BOUNDS = (-124.831355, 24.485906, -66.851641, 49.571293)

# Static global imagery avoids a date-dependent GIBS request while providing a
# dependable fallback wherever NAIP has no coverage.
NASA_GIBS_RASTER_TILE_URL = (
    "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/"
    "BlueMarble_ShadedRelief_Bathymetry/default/default/"
    "GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg"
)
NASA_GIBS_RASTER_TILE_MAX_ZOOM = 8


# Both map documents expose the same small JavaScript API. The surrounding
# renderer defines ``map``, ``basemapConfig``, and ``basemapAttributionElement``.
BASEMAP_RUNTIME_JAVASCRIPT = r"""
    let activeBasemap = basemapConfig.selected === "imagery" ? "imagery" : "osm";
    let imageryOpacity = clampBasemapOpacity(basemapConfig.imageryOpacity);
    let naipFailed = false;
    let gibsFailed = false;

    function clampBasemapOpacity(value) {
      const numeric = Number(value);
      return Number.isFinite(numeric) ? Math.max(0, Math.min(1, numeric)) : 1;
    }

    function basemapRasterLayers() {
      const imageryVisibility = activeBasemap === "imagery" ? "visible" : "none";
      return [
        {
          id: "osm-raster-base",
          type: "raster",
          source: "osm-raster-source",
          minzoom: 0,
          paint: {"raster-opacity": 1, "raster-fade-duration": 0}
        },
        {
          id: "nasa-gibs-raster-base",
          type: "raster",
          source: "nasa-gibs-raster-source",
          minzoom: 0,
          layout: {visibility: imageryVisibility},
          paint: {"raster-opacity": imageryOpacity, "raster-fade-duration": 0}
        },
        {
          id: "usgs-naip-raster-base",
          type: "raster",
          source: "usgs-naip-raster-source",
          minzoom: 0,
          layout: {visibility: imageryVisibility},
          paint: {"raster-opacity": imageryOpacity, "raster-fade-duration": 0}
        }
      ];
    }

    function centerInsideNaip() {
      if (!map || !Array.isArray(basemapConfig.naipBounds) || basemapConfig.naipBounds.length !== 4) {
        return false;
      }
      const center = map.getCenter();
      const bounds = basemapConfig.naipBounds;
      return center.lng >= bounds[0] && center.lng <= bounds[2]
        && center.lat >= bounds[1] && center.lat <= bounds[3];
    }

    function activeImageryProvider() {
      if (activeBasemap !== "imagery") return "osm";
      if (!naipFailed && centerInsideNaip()) return "naip";
      return gibsFailed ? "osmFallback" : "gibs";
    }

    function naipAttributionRequired() {
      // NAIP can remain visible in the edge of a viewport even when the map
      // center lies outside its published bounds. Credit it conservatively
      // whenever the layer is enabled instead of inferring solely from center.
      return activeBasemap === "imagery" && !naipFailed;
    }

    function updateBasemapAttribution() {
      const provider = activeImageryProvider();
      const attributionProvider = naipAttributionRequired() ? "naip" : provider;
      if (basemapAttributionElement) {
        basemapAttributionElement.innerHTML = basemapConfig.attribution[attributionProvider]
          || basemapConfig.attribution.osm;
      }
      return provider;
    }

    function reapplyBasemapState() {
      // MapLibre loads inline style JSON on a later animation frame. Reapply
      // the current state when those layers appear without treating that
      // lifecycle event as an explicit provider retry.
      if (map) {
        const gibsVisibility = activeBasemap === "imagery" && !gibsFailed ? "visible" : "none";
        const naipVisibility = activeBasemap === "imagery" && !naipFailed ? "visible" : "none";
        if (map.getLayer("nasa-gibs-raster-base")) {
          map.setLayoutProperty("nasa-gibs-raster-base", "visibility", gibsVisibility);
          map.setPaintProperty("nasa-gibs-raster-base", "raster-opacity", imageryOpacity);
        }
        if (map.getLayer("usgs-naip-raster-base")) {
          map.setLayoutProperty("usgs-naip-raster-base", "visibility", naipVisibility);
          map.setPaintProperty("usgs-naip-raster-base", "raster-opacity", imageryOpacity);
        }
        if (typeof map.triggerRepaint === "function") map.triggerRepaint();
      }
      updateBasemapAttribution();
      return activeBasemap;
    }

    function setBasemap(value) {
      const requested = String(value || "").toLowerCase() === "imagery" ? "imagery" : "osm";
      if (requested === "imagery") {
        // Selecting imagery again explicitly retries providers that may have
        // failed earlier in this document.
        naipFailed = false;
        gibsFailed = false;
      }
      activeBasemap = requested;
      return reapplyBasemapState();
    }

    function setImageryOpacity(value) {
      imageryOpacity = clampBasemapOpacity(value);
      reapplyBasemapState();
      return imageryOpacity;
    }

    function basemapSourceId(event) {
      if (!event || typeof event !== "object") return "";
      if (event.sourceId) return String(event.sourceId);
      if (event.source && event.source.id) return String(event.source.id);
      if (event.error && event.error.sourceId) return String(event.error.sourceId);
      if (event.error && event.error.source && event.error.source.id) {
        return String(event.error.source.id);
      }
      return "";
    }

    function handleBasemapError(event) {
      const sourceId = basemapSourceId(event);
      if (sourceId === "usgs-naip-raster-source") {
        naipFailed = true;
        reapplyBasemapState();
        return true;
      }
      if (sourceId === "nasa-gibs-raster-source") {
        gibsFailed = true;
        reapplyBasemapState();
        return true;
      }
      if (sourceId === "osm-raster-source") {
        return activeBasemap === "imagery"
          && ((!naipFailed && centerInsideNaip()) || !gibsFailed);
      }
      return false;
    }

    function basemapDebugState() {
      return {
        selected: activeBasemap,
        imageryOpacity,
        provider: activeImageryProvider(),
        attribution: basemapAttributionElement ? basemapAttributionElement.textContent.trim() : "",
        layers: {
          osm: Boolean(map && map.getLayer("osm-raster-base")),
          gibs: Boolean(map && map.getLayer("nasa-gibs-raster-base")),
          naip: Boolean(map && map.getLayer("usgs-naip-raster-base"))
        }
      };
    }
"""


def normalize_basemap(value: object) -> str:
    """Return a supported basemap identifier, defaulting to OpenStreetMap."""
    return BASEMAP_IMAGERY if str(value or "").strip().lower() == BASEMAP_IMAGERY else BASEMAP_OPENSTREETMAP


def clamp_imagery_opacity(value: object) -> float:
    """Return a finite imagery opacity in the inclusive ``0..1`` range."""
    try:
        numeric = (
            float(value)
            if isinstance(value, (int, float, str))
            else DEFAULT_IMAGERY_OPACITY
        )
    except (TypeError, ValueError):
        numeric = DEFAULT_IMAGERY_OPACITY
    if not math.isfinite(numeric):
        numeric = DEFAULT_IMAGERY_OPACITY
    return max(0.0, min(1.0, numeric))


def build_basemap_config(payload: Mapping[str, object] | None = None) -> dict[str, object]:
    """Build the JSON-safe MapLibre configuration shared by both map views."""
    data = payload or {}
    selected = normalize_basemap(
        data.get("basemap", data.get("base_map", data.get("map_basemap", BASEMAP_OPENSTREETMAP)))
    )
    opacity = clamp_imagery_opacity(
        data.get(
            "imagery_opacity",
            data.get("imageryOpacity", DEFAULT_IMAGERY_OPACITY),
        )
    )
    osm_attribution = (
        'Map tiles &copy; <a href="https://www.openstreetmap.org/copyright">'
        "OpenStreetMap contributors</a>"
    )
    naip_attribution = (
        '<a href="https://imagery.nationalmap.gov/arcgis/rest/services/'
        'USGSNAIPImagery/ImageServer">USGS/USDA NAIP, The National Map</a>'
    )
    gibs_attribution = (
        '<a href="https://nasa-gibs.github.io/gibs-api-docs/">NASA GIBS</a>'
    )
    return {
        "selected": selected,
        "imageryOpacity": opacity,
        "naipBounds": list(USGS_NAIP_BOUNDS),
        "sources": {
            "osm-raster-source": {
                "type": "raster",
                "tiles": [OPENSTREETMAP_RASTER_TILE_URL],
                "tileSize": 256,
                "minzoom": 0,
                "maxzoom": OPENSTREETMAP_RASTER_TILE_MAX_ZOOM,
                "attribution": osm_attribution,
            },
            "nasa-gibs-raster-source": {
                "type": "raster",
                "tiles": [NASA_GIBS_RASTER_TILE_URL],
                "tileSize": 256,
                "minzoom": 0,
                "maxzoom": NASA_GIBS_RASTER_TILE_MAX_ZOOM,
                "attribution": gibs_attribution,
            },
            "usgs-naip-raster-source": {
                "type": "raster",
                "tiles": [USGS_NAIP_WMS_TILE_URL],
                "tileSize": 256,
                "minzoom": 0,
                "maxzoom": USGS_NAIP_RASTER_TILE_MAX_ZOOM,
                "bounds": list(USGS_NAIP_BOUNDS),
                "attribution": naip_attribution,
            },
        },
        "attribution": {
            "osm": osm_attribution,
            "naip": (
                f"{naip_attribution} &middot; {gibs_attribution} fallback &middot; "
                f"Final fallback: {osm_attribution}"
            ),
            "gibs": f"{gibs_attribution} &middot; Final fallback: {osm_attribution}",
            "osmFallback": f"{osm_attribution} (imagery unavailable)",
        },
    }
