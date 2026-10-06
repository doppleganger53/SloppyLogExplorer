# Attribution Notice

## Application Icon

The aircraft-and-telemetry icon was created for Sloppy Log Explorer using OpenAI image generation. No third-party logo assets were reused. The PNG artwork and multi-resolution Windows ICO are included under `sloppy_log_explorer/assets/`.

## Ethos_LogView

Sloppy Log Explorer uses `Ethos_LogView` as its starting point for a Python/PyQt telemetry log viewer architecture, including the general approach of parsing Ethos CSV data with pandas and rendering interactive Plotly graphs in a Qt desktop application.

- Source project: `Ethos_LogView`
- Upstream repository: https://github.com/BladeScraper-Designs/Ethos_LogView
- Local fork observed for this work: https://github.com/doppleganger53/Ethos_LogView
- Commit used as reference: `449030c42d9b5bf3189eb12983cc19dabc272c82`
- License: GNU General Public License v3.0

No assets from `Ethos_LogView/img` are included in this repository. The implementation in this repository is organized as a new application and retains GPL-compatible licensing.

## Feature Reference

The feature target was the publicly documented behavior and screenshots for PhaedraDG's "Ethos log explorer" on itch.io, including model-organized browsing, graph inspection, compare mode, GPS path viewing, battery health history, SD-card sync, flight notes/video links, switch aliases, and voice-pack generation. No proprietary code or downloadable binary content from that tool is included.

## MapLibre And Raster Basemaps

The Flight Map and HeatMap use MapLibre GL JS 6.4.1, bundled in `sloppy_log_explorer/assets/maplibre/`, with selectable OpenStreetMap and public imagery raster layers. The official release files and their license are included; `VERSION.txt` records the source archive and verified integrity hash.

- MapLibre GL JS: https://maplibre.org/maplibre-gl-js/docs/
- License: BSD 3-Clause License
- OpenStreetMap attribution: Tiles and map data copyright OpenStreetMap contributors
- OpenStreetMap copyright and license: https://www.openstreetmap.org/copyright
- USGS/USDA NAIP service: https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPImagery/ImageServer
- NAIP source/usage: Public domain; service attribution is USGS, USDA, and The National Map
- NASA Global Imagery Browse Services (GIBS): https://nasa-gibs.github.io/gibs-api-docs/
- NASA acknowledgment: Imagery is provided by NASA GIBS, part of NASA's Earth Science Data and Information System (ESDIS)

The application does not use Google Maps, Mapbox, paid subscriptions, account signups, or API keys for map visualization. OpenStreetMap is the default and final fallback. Imagery mode places NASA GIBS beneath transparent NAIP ArcGIS image-export tiles, so the active view may request tiles from all three providers.
