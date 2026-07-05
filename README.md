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

## License And Attribution

Sloppy Log Explorer is licensed under GPL-3.0-or-later. See [LICENSE](LICENSE).

This project is derived from and inspired by `Ethos_LogView`, which is GPLv3 licensed. See [NOTICE.md](NOTICE.md) for attribution and source details.

Sloppy Log Explorer is not affiliated with, authorized, sponsored, or endorsed by FrSky Electronic Co., Ltd. FrSky and ETHOS are trademarks of their respective owner.
