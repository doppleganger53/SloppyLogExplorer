# Sloppy Log Explorer

Sloppy Log Explorer is a desktop telemetry application for FrSky Ethos and OpenTX CSV logs. It uses `Ethos_LogView` as the starting point for the parsing and Plotly/PyQt direction, then adds the broader workflow expected from a full log explorer: model-organized log browsing, interactive graph inspection, compare mode, GPS flight-path viewing, battery health tracking, SD-card sync, flight notes, switch aliases, and voice-pack generation.

## Features

- Browse a log library grouped by model.
- Open individual Ethos/OpenTX CSV or log files.
- Select telemetry parameters and view a native multi-series telemetry graph.
- Click the graph or use left/right arrow keys in the graph to inspect telemetry values in the cursor panel.
- Load a comparison flight and view matching parameter deltas.
- View detected GPS latitude/longitude/altitude on a 3D MapLibre/OpenStreetMap flight map.
- Color the GPS flight path by any numeric telemetry parameter with custom start/end colors and range controls.
- Step through the GPS flight path with play/pause timeline controls, playback speeds, and synchronized marker telemetry.
- Detect flying sites across the current library and build 5 m, multi-flight reception maps with live opacity, indexed-channel grouping, and optional telemetry normalization.
- Store flight notes and link a local video file to each flight.
- Register batteries, calculate pack and per-cell internal resistance from voltage/current logs, and retain health history.
- Sync newer log files from a radio SD card folder into a local PC log library.
- Manage switch alias profiles for mapping hardware switch names to user-facing labels.
- Build voice-pack CSVs and generate WAV files with `pyttsx3` when a local TTS engine is available.
- Persist app state in the user profile, not inside the Git checkout.

## Install And Run

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

The recommended Windows distribution is the minimal PyInstaller `onedir` target. It starts faster than a single-file executable, handles Qt WebEngine more reliably, and excludes optional Plotly/Kaleido/devtools content that is not required for normal telemetry graphs, GPS maps, or voice-pack generation.

```powershell
.\build_windows.bat
```

The executable is written to:

```text
dist\SloppyLogExplorer\SloppyLogExplorer.exe
```

To build the same target directly:

```powershell
python -m pip install -r requirements.txt
python build.py --clean --target minimal
```

To build the broader diagnostics package, use the debug target. It keeps broad Plotly collection enabled for packaging investigation and may report harmless optional-import warnings when optional tools such as matplotlib are not installed.

```powershell
.\build_windows.bat --target debug
```

To build a single executable instead:

```powershell
.\build_windows.bat --onefile
```

Direct Python usage supports the same target and mode flags:

```powershell
python build.py --clean --target debug --onefile
```

## Data And Storage

Application state is stored under `%APPDATA%\SloppyLogExplorer` on Windows or `~/.sloppy_log_explorer` on other platforms. The app stores settings, flight notes, video links, battery history, and switch aliases in a local SQLite database.

The flight map tab uses locally bundled MapLibre GL JS assets with OpenStreetMap raster tiles for Qt WebEngine tile-refresh reliability. It does not require Google Maps, Mapbox, paid subscriptions, account signups, or API keys. The map tiles require normal internet access and are cached by Qt WebEngine under the app data directory.

The Reception Map tab keeps the normal library tree scan metadata-only, then refreshes a separate GPS/site index in one background worker. Unchanged files are reused from the local SQLite cache. Logs whose headers identify a position field receive one whole-file, every-twentieth-GPS-update pass so late or phase-offset GPS acquisition is still found; other logs use a sparse 200-record fallback for unhinted coordinate formats and are skipped when that finds no valid GPS. Site centroids use the dominant local fix cloud so partial-lock and distant corrupt fixes do not create false sites. Generating a selected heatmap reads the matching logs and summarizes observed samples into 5 m cells, giving each flight equal weight. Duplicate telemetry headers represented by terminal `.1`, `.2`, and later indices are combined per row, and an optional paired telemetry item can normalize the mapped values by ratio, difference, or dB power correction. Ratio references can use their observed range or user-supplied clamping bounds. The automatic camera centers on the densest observed sample neighborhood and never opens wider than a 2 km radius, while the complete generated cell layer remains available for manual navigation. Flying-site names, notes, dates, centroids, raw channel names, file fingerprints, and index versions stay in the local application database; raw or normalized reception samples are not persisted. Telemetry values remain local, although OpenStreetMap tile requests necessarily identify the viewed map area to the tile provider.

## License And Attribution

Sloppy Log Explorer is licensed under GPL-3.0-or-later. See [LICENSE](LICENSE).

This project is derived from and inspired by `Ethos_LogView`, which is GPLv3 licensed. See [NOTICE.md](NOTICE.md) for attribution and source details.

Sloppy Log Explorer is not affiliated with, authorized, sponsored, or endorsed by FrSky Electronic Co., Ltd. FrSky and ETHOS are trademarks of their respective owner.
