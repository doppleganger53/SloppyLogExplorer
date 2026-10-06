# Sloppy Log Explorer

Sloppy Log Explorer is a desktop telemetry application for FrSky Ethos and OpenTX CSV logs. It uses `Ethos_LogView` as the starting point for the parsing and Plotly/PyQt direction, then adds the broader workflow expected from a full log explorer: model-organized log browsing, interactive graph inspection, compare mode, GPS flight-path viewing, battery health tracking, SD-card sync, flight notes, switch aliases, and voice-pack generation.

## Features

- Browse a log library grouped by model.
- Open individual Ethos/OpenTX CSV or log files.
- Select telemetry parameters and view a native multi-series telemetry graph.
- Click the graph or use left/right arrow keys in the graph to inspect telemetry values in the cursor panel.
- Load a comparison flight and view matching parameter deltas.
- View detected GPS latitude/longitude/altitude on a 3D MapLibre flight map with selectable OpenStreetMap or aerial/satellite imagery.
- Color the GPS flight path by any numeric telemetry parameter with custom start/end colors and range controls.
- Measure ground distance on the Flight Map with editable points and metric/imperial totals.
- Step through the GPS flight path with play/pause timeline controls, playback speeds, and synchronized marker telemetry.
- Detect flying sites across the current library and build 5 m, multi-flight HeatMaps with live overlay and imagery opacity, indexed-channel grouping, and optional telemetry normalization.
- Store flight notes and link a local video file to each flight.
- Register batteries, calculate pack and per-cell internal resistance from voltage/current logs, and retain health history.
- Sync newer log files from a radio SD card folder into a local PC log library.
- Manage switch alias profiles for mapping hardware switch names to user-facing labels.
- Build voice-pack CSVs and generate WAV files with `pyttsx3` when a local TTS engine is available.
- Persist app state in the user profile, not inside the Git checkout.

## Install And Run

Source installations require Python 3.10 or newer. Qt and Qt WebEngine 6.11.2
or newer are required to include current security fixes. The portable Windows
executable includes Python and Qt; no separate installation is needed.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

For development:

```powershell
python -m pip install -e .[dev]
python -m pytest
```

## Build A Windows Executable

The release Windows distribution is the minimal PyInstaller `onefile` target. It produces one portable executable and excludes optional Plotly/Kaleido/devtools content that is not required for normal telemetry graphs, GPS maps, or voice-pack generation. The `onedir` target remains available for local development when faster startup or Qt WebEngine troubleshooting is more important than portability.

```powershell
.\build_windows.bat
```

The release executable is written to:

```text
dist\SloppyLogExplorer.exe
```

The release archive also includes `README.md`, `NOTICE.md`, `LICENSE`, and
`CHANGELOG.md`.

The application icon is embedded in every Windows build and bundled for the app's windows and taskbar. Source installations include the same icon. Artwork lives in `sloppy_log_explorer/assets/app-icon.png`; regenerate its 16–256 px Windows ICO after artwork changes with `python tools/make_app_icon.py`. No extra imaging dependency is required.

To build the same target directly:

```powershell
python -m pip install -r requirements.txt
python build.py --clean --target minimal --onefile
```

To build the broader diagnostics package, use the debug target. It keeps broad Plotly collection enabled for packaging investigation and may report harmless optional-import warnings when optional tools such as matplotlib are not installed.

```powershell
.\build_windows.bat --target debug
```

To build the onedir development target instead:

```powershell
python build.py --clean --target minimal
```

Direct Python usage supports the same target and mode flags:

```powershell
python build.py --clean --target debug --onefile
```

## Data And Storage

Application state is stored under `%APPDATA%\SloppyLogExplorer` on Windows or `~/.sloppy_log_explorer` on other platforms. The app stores settings, flight notes, video links, battery history, and switch aliases in a local SQLite database.

The Flight Map and HeatMap tabs use locally bundled MapLibre GL JS assets and default to OpenStreetMap raster tiles. Both tabs also provide an `Imagery (NAIP → NASA GIBS)` basemap: transparent USGS/USDA NAIP imagery is shown where the service has coverage, NASA GIBS Blue Marble is the global imagery fallback, and OpenStreetMap remains underneath as the final recovery layer. NAIP is primarily high-resolution conterminous-U.S. aerial imagery. Its ArcGIS image-export tiles load at the current zoom up to level 19; further zooming reuses that finest detail. An individual unavailable tile preserves the remaining imagery and available parent tiles. The static GIBS fallback is global but has a much coarser native resolution (about 611 m per pixel at its maximum Web Mercator level), so it will look pixelated at field-scale zoom. Basemap selection and imagery opacity update in place without resetting the current camera or map overlays.

To measure ground distance, select **Measure** on the Flight Map and click two or more locations. The total follows the sequence of points and appears in metres/kilometres and feet/miles. Drag a numbered point to move it, click it to remove it, or use **Undo** and **Clear**. Select **Done** or press **Esc** to finish editing while keeping the measurement visible. Distances follow the ground coordinates and do not include flight altitude.

These map services require normal internet access and are cached by Qt WebEngine under the app data directory. Imagery mode can contact USGS, NASA, and OpenStreetMap concurrently because the fallback layers are stacked. Each tile request necessarily reveals the viewed map area and network address to the applicable provider. No Google Maps, Mapbox, paid subscription, account sign-in, API key, or additional Python dependency is required. Provider details and attribution are listed in `NOTICE.md` and remain visible in each map view.

The HeatMap tab keeps the normal library tree scan metadata-only, then refreshes a separate GPS/site index in one background worker. Unchanged files are reused from the local SQLite cache. Logs whose headers identify a position field receive one whole-file, every-twentieth-GPS-update pass so late or phase-offset GPS acquisition is still found; other logs use a sparse 200-record fallback for unhinted coordinate formats and are skipped when that finds no valid GPS. Site centroids use the dominant local fix cloud so partial-lock and distant corrupt fixes do not create false sites. Generating a selected heatmap reads the matching logs and summarizes observed samples into 5 m cells, giving each flight equal weight. Duplicate telemetry headers represented by terminal `.1`, `.2`, and later indices are combined per row, and an optional paired telemetry item can normalize the mapped values by ratio, difference, or dB power correction. Ratio references can use their observed range or user-supplied clamping bounds. Additional reference controls stay hidden until normalization is enabled. The automatic camera centers on the densest observed sample neighborhood and never opens wider than a 2 km radius, while the complete generated cell layer remains available for manual navigation. Flying-site names, notes, dates, centroids, raw channel names, file fingerprints, and index versions stay in the local application database; raw or normalized reception samples are not persisted. Telemetry values remain local.

## License And Attribution

Sloppy Log Explorer is licensed under GPL-3.0-or-later. See [LICENSE](LICENSE).

This project is derived from and inspired by `Ethos_LogView`, which is GPLv3 licensed. See [NOTICE.md](NOTICE.md) for attribution and source details.

Sloppy Log Explorer is not affiliated with, authorized, sponsored, or endorsed by FrSky Electronic Co., Ltd. FrSky and ETHOS are trademarks of their respective owner.
