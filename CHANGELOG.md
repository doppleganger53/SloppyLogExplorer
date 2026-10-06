# Changelog

All notable changes to Sloppy Log Explorer are documented in this file.

## [Unreleased]

## [0.2.1] - 2026-10-06

### Added

- Add an aircraft-and-telemetry app icon for Windows Explorer, app windows, and the taskbar, embedded in the portable executable and included in source packages.
- Add a Flight Map ground-distance tool with multiple points, draggable endpoints, click-to-remove, undo, clear, and metric/imperial totals (#39).

### Fixed

- Remove solid ground-to-altitude walls from the Flight Map, preserving the elevated, telemetry-colored flight path. Validate the ribbon geometry and allow live map checks over a complete flight.
- Refresh NAIP imagery as the map zooms, retain available parent tiles after individual tile errors, and reuse the finest native detail above the imagery source's maximum zoom (#40).

## [0.2.0] - 2026-09-27

### Added

- GitHub issue and release workflow support for agent-assisted development.
- Added a shared `Imagery (NAIP → NASA GIBS)` basemap with OpenStreetMap fallback, live imagery opacity, and provider attribution to Flight Map and HeatMap.
- Added repeatable release UX evaluation with isolated synthetic flights, both themes, and desktop-size screenshots.

### Changed

- Renamed the Reception Map tab to HeatMap and hide optional reference inputs until normalization is enabled.
- Improved large-log GPS sampling and timeline processing, and aligned comparison cursor values by time.
- Required Python 3.10+, Plotly 7.1+, and Qt/Qt WebEngine 6.11.2+ for graph layout and security fixes.

### Fixed

- Reapply the latest HeatMap opacity and color-range controls after the current WebEngine document finishes loading.
- Prevent crafted telemetry text from injecting scripts or renderer template content into maps.
- Upgrade the bundled MapLibre runtime to 6.4.1 to address CVE-2026-85061, and migrate the local map views to its module-based distribution.
- Explicitly disable Plotly's cloud-sharing controls so telemetry stays local.
- Preserve all duplicate telemetry channels, detect comma/semicolon/tab files, and handle missing, nonfinite, extreme, and midnight-crossing timestamps.
- Protect existing logs during interrupted SD sync and reject overlapping directories, stale candidates, and paths outside the selected library.
- Preserve existing voice files when speech generation fails; report failures instead of substituting alarm tones, and validate Windows filenames.
- Protect unsaved flight notes, retain alias drafts and manual map ranges, and report disk/CSV errors in the UI.
- Improve compact-window layout, multi-axis graph readability, and map camera persistence.
- Retain telemetry axis grouping across log changes within a session, without persisting it across application launches (#36).
- Correct Windows build argument forwarding, build output documentation, displayed version, and atomic release-archive creation.
- Isolate the Windows build DLL search path so unrelated tools cannot contribute incompatible libraries to the portable executable.

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
