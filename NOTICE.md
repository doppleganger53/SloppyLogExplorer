# Attribution Notice

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

## CesiumJS And OpenStreetMap

The 3D flight-path globe loads CesiumJS release assets from Cesium's public release CDN and uses `OpenStreetMapImageryProvider` with the public OpenStreetMap tile service.

- CesiumJS: https://cesium.com/platform/cesiumjs/
- License: Apache License 2.0
- OpenStreetMap tile service: https://tile.openstreetmap.org/
- OpenStreetMap attribution: Tiles and map data copyright OpenStreetMap contributors

The application does not use Cesium ion, Google Maps, Mapbox, paid subscriptions, account signups, or API keys for the GPS visualization.
