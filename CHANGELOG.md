# Changelog

All notable changes to Sloppy Log Explorer are documented in this file.

## [Unreleased]

### Added

- GitHub issue and release workflow support for agent-assisted development.
- Added a shared `Imagery (NAIP → NASA GIBS)` basemap with OpenStreetMap fallback, live imagery opacity, and provider attribution to Flight Map and HeatMap.

### Changed

- Renamed the Reception Map tab to HeatMap and hide optional reference inputs until normalization is enabled.

### Fixed

- Reapply the latest HeatMap opacity and color-range controls after the current WebEngine document finishes loading.

## [0.1.0] - 2026-06-19

### Added

- Initial desktop telemetry explorer baseline with log parsing, native telemetry
  graphs, model-organized library browsing, battery tracking, SD-card sync,
  switch aliases, and voice-pack tooling.
- Added cursor-based telemetry inspection and comparison-flight parameter
  deltas.
- Added MapLibre/OpenStreetMap GPS flight maps with telemetry coloring and
  synchronized timeline playback.
- Added persistent flight notes with local video links.
- Added flying-site detection and 5 m multi-flight reception heatmaps with
  indexed-channel grouping and optional telemetry normalization.
