# Sloppy Log Explorer

Sloppy Log Explorer is a desktop telemetry application for FrSky Ethos and OpenTX CSV logs. It uses `Ethos_LogView` as the starting point for the parsing and Plotly/PyQt direction, then adds the broader workflow expected from a full log explorer: model-organized log browsing, interactive graph inspection, compare mode, GPS flight-path viewing, battery health tracking, SD-card sync, flight notes, switch aliases, and voice-pack generation.

## Features

- Browse a log library grouped by model.
- Open individual Ethos/OpenTX CSV or log files.
- Select telemetry parameters and view a native multi-series telemetry graph.
- Drag mostly horizontally on Telemetry to zoom in on a time interval while keeping the Y scales. Drag a taller rectangle to zoom both time and values.
- Click the graph or use left/right arrow keys in the graph to inspect telemetry values in the cursor panel.
- Load a comparison flight and view matching parameter deltas.
- View detected GPS latitude/longitude/altitude on a 3D MapLibre flight map with selectable OpenStreetMap or aerial/satellite imagery.
- Color the GPS flight path by any numeric telemetry parameter with custom start/end colors and range controls.
- Measure ground distance on the Flight Map with editable points and metric/imperial totals.
- Step through the GPS flight path with play/pause timeline controls, playback speeds, and synchronized marker telemetry.
- Detect flying sites across the current library and build 5 m, multi-flight HeatMaps with live overlay and imagery opacity, indexed-channel grouping, and optional telemetry normalization.
- Store flight notes and link a local video file to each flight.
- Generate and maintain per-aircraft flight logs with a reviewed detection preview, aircraft selection, editable pilot notes, and CSV export.
- Drag flight-log column headings to choose the order saved in Markdown, CSV and Google Sheets.
- Configure default and per-aircraft telemetry metrics, and sign in with a personal Google account to create or sync a Google Sheets workbook view.
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

Flight Map and HeatMap share a local cache for NAIP and NASA imagery. When you revisit a zoom level, either tab can draw fresh tiles from the cache. The app retries temporary download failures and can show previously downloaded imagery during an outage when the provider's cache rules permit it. The cache holds up to 384 tiles (96 MiB of image data). Tiles are eligible for reuse for up to seven days.

The HeatMap tab keeps the normal library tree scan metadata-only, then refreshes a separate GPS/site index in one background worker. Unchanged files are reused from the local SQLite cache. Logs whose headers identify a position field receive one whole-file, every-twentieth-GPS-update pass so late or phase-offset GPS acquisition is still found; other logs use a sparse 200-record fallback for unhinted coordinate formats and are skipped when that finds no valid GPS. Site centroids use the dominant local fix cloud so partial-lock and distant corrupt fixes do not create false sites. Generating a selected heatmap reads the matching logs and summarizes observed samples into 5 m cells, giving each flight equal weight. Duplicate telemetry headers represented by terminal `.1`, `.2`, and later indices are combined per row, and an optional paired telemetry item can normalize the mapped values by ratio, difference, or dB power correction. Ratio references can use their observed range or user-supplied clamping bounds. Additional reference controls stay hidden until normalization is enabled. The automatic camera centers on the densest observed sample neighborhood and never opens wider than a 2 km radius, while the complete generated cell layer remains available for manual navigation. Flying-site names, notes, dates, centroids, raw channel names, file fingerprints, and index versions stay in the local application database; raw or normalized reception samples are not persisted. Telemetry values remain local.

## Flight Logs

Select **Flight Logs** (also available through **Tools → Manage Flight Logs**),
then use **Open library…** if a library is not already open. The
workspace has four tabs:

1. **Add flights:** select aircraft and choose **Review flights**. Filename
   prefixes identify aircraft, with the first library subfolder as fallback;
   use **Source aircraft** to correct mappings. Confirmed flights are included
   automatically. **Needs review only** focuses on uncertain flights; use the
   row's Accept/Reject choice, or select rows and choose **Accept selected** /
   **Reject selected**. **Next to review** moves to the next unresolved flight.
   Review counts, warnings and **Flight-log preview**, then choose **Save new
   flights**. Saving opens the maintained log. Use **Detection details and
   warnings** for the complete source report.
   After reviewing the selected aircraft, choose **Archive non-flight logs…** to
   see eligible recordings. Check the files to move and choose **Archive checked
   logs**. Find them under `non-flight telemetry` in the library, with their
   original subfolders and filenames. Move a file back to its original folder
   and refresh the library to restore it. SD sync recognizes an identical
   archived recording and still offers a changed recording with the same name.
   Existing archives under `flight-logs/non-flight telemetry` are also recognized.
2. **Flight logs:** view maintained aircraft tables, edit pilot Notes, save changes, and
   export CSV. Choose an existing skill-generated flight-log directory to
   continue maintaining those Markdown files. Drag headings, then choose
   **Save column order** to reorder the existing table without recalculating
   flights, changing numbers, or losing notes. New logs can be arranged by
   dragging headings in **Add flights → Flight-log preview** before saving.
   CSV export and per-aircraft Sheets tabs use the saved order. The combined
   Sheets view follows the first aircraft's order, with other columns added.
   Right-click a maintained row and choose **Open flight telemetry** to view
   its source recording at that flight's interval. If several recordings match,
   the app lets you choose the source.
3. **Telemetry:** configure defaults and aircraft-specific overrides. Automatic
   VFR minima and temperature maxima use only sensors present in accepted
   telemetry. **Capture paired servo-current maxima** is enabled by default:
   each named current channel (A/mA) must have a temperature channel with the
   same custom name, such as `ELE L curr(A)` / `ELE L temp(°F)` or
   `SRV3 curr(A)` / `SRV3 temp(°C)`. Known ESC, BEC, battery, receiver and motor
   names are excluded from automatic servo detection. Each maximum keeps its
   exact source header and units and uses only samples within the flight.
   The checkbox can be disabled per aircraft; individual exclusions still win.
   Additional maxima and exclusions use exact source headers and
   retain units, for example `Current(A)` and `Temp2(°C)`. Exclusions win.
4. **Google Sheets:** choose **Sign in with Google**, create or link a workbook,
   then choose **Review sync**. Check the aircraft/flight counts in the workspace
   and choose **Sync flights now** to publish an all-aircraft view plus individual
   aircraft tabs. Edited Sheets Notes stay
   in the workbook; use **Import reviewed Sheets Notes** to bring them back into
   the local Markdown logs after reviewing the changes.

Logs default to the app data directory's `flight-logs` folder. Sensor choices
are stored beside the tables in `.flight-log-settings.json`. The app remembers
aircraft selections and candidate decisions in its local database. Normal
generation appends new chronological flights, keeps the existing schema and pilot notes,
and detects repeat flights by date/time plus duration. Earlier newly discovered
flights are reported as conflicts. Changing telemetry choices affects new tables;
adding or removing an existing table's columns requires a rebuild preview and explicit
approval of schema changes, removed rows, and unmatched notes. Rebuild is a
separate operation, not an automatic conflict resolution. **Review rebuild…**
shows a preview first. **Review and approve rebuild…** opens the final review;
**Approve and rebuild** is the explicit authorization. Rearranging existing
columns uses **Save column order** and does not require a rebuild.

The app rechecks source and output files before writing so a preview cannot
silently overwrite changes made since it was scanned. Flight detection uses
control, reception, propulsion/motion, and airborne evidence. Sensor-poor or
unusual flights still need pilot review; an accepted uncertain flight keeps its
uncertainty note. All telemetry analysis and Markdown generation run locally.

### Google Sheets Setup

**Personal Gmail accounts work; Google Workspace is not required.** The app
opens your normal browser so you can choose your Google account and approve
access. The desktop app needs its own registered OAuth client; the Google
connection inside ChatGPT belongs to ChatGPT and cannot be reused here.

Until a distributor supplies the app's registered client, choose **Set up Google
sign-in…** or **One-time setup guide…** in the Google Sheets tab. The five-step
guide has clickable Google Console links and walks through:

1. Create a Google Cloud project named **Sloppy Log Explorer** using your Gmail
   account. Keep the same project selected through the remaining steps.
2. Enable **Google Sheets API**.
3. Open **Google Auth platform → Branding → Get started**. Enter the app name
   and your Gmail address for support/contact. Choose **External** for Audience,
   then add your Gmail address under **Audience → Test users**. Under
   **Data Access → Add or remove scopes**, add
   `https://www.googleapis.com/auth/spreadsheets` and save.
4. Open **Google Auth platform → Clients → Create client**. Choose **Desktop
   app**, create it, then open the client details and **Download JSON**.
5. Select that downloaded file on the guide's final page and choose **Finish**.
   Keep it outside the repository. Then choose **Sign in with Google** and
   approve access in your browser.

Google's [consent-screen setup](https://developers.google.com/workspace/guides/configure-oauth-consent)
and [desktop OAuth guide](https://developers.google.com/identity/protocols/oauth2/native-app)
describe these requirements. Google labels the documentation “Workspace”
because Sheets is a Workspace API; External OAuth also supports personal accounts.
An app still in **Testing** may require sign-in again after seven days, per
Google's [refresh-token expiration rules](https://developers.google.com/identity/protocols/oauth2#expiration).

The app requests Sheets access plus your email address to identify the connected
account. The Sheets permission allows access to spreadsheets your account can
edit; app sync writes only its managed flight-log tabs. Choose **Remember
sign-in securely** to retain authorization in **Windows Credential Manager**.
The app restores and refreshes that authorization when you next connect or sync.
If secure storage is unavailable, authorization lasts for the current app
session and the sign-in status explains that. **Disconnect** removes saved
authorization from this computer. Google permissions can also be revoked in
your [Google Account connections](https://myaccount.google.com/connections).

For distributors, a registered Desktop OAuth client can be supplied at
`sloppy_log_explorer/assets/google-oauth-client.json` (included by the package
and Windows build when present), or with the `SLE_GOOGLE_OAUTH_CLIENT` environment
variable pointing to its JSON. That removes individual users' Cloud setup;
users then go directly to **Sign in with Google**. No registered client is
included in this repository.

For existing service-account integrations, expand **Advanced connection
settings**, choose the JSON, and link an existing workbook
shared with that account's `client_email` as an **Editor**. Service accounts
cannot create ordinary My Drive workbooks because they have no personal Drive
storage quota; use desktop OAuth to create a workbook. See
[Google service-account guidance](https://developers.google.com/workspace/drive/api/guides/handle-errors#storageQuotaExceeded).

Sync uploads the maintained flight tables, aircraft names, telemetry summary
metrics, and Notes to Google. Raw telemetry recordings, GPS tracks, and local
source paths are not uploaded. The app stores client-file paths and workbook
preferences locally. Authorization tokens stay in memory or Windows Credential
Manager, never in plaintext settings or files. Keep credential JSON files
outside the repository and
do not share them. Sync writes only app-managed
tabs; unrelated workbook tabs remain available for your own work.

Local tables own generated metrics. Notes edits and deletions in either the
combined or aircraft view are preserved; conflicting edits stop sync before
writing. Finish any workbook edits before starting a sync. The app rechecks
remote data before writing, but Google Sheets does not provide a conditional
write across this multi-request workflow. Local files are also checked before
sync and before importing reviewed Notes.

## License And Attribution

Sloppy Log Explorer is licensed under GPL-3.0-or-later. See [LICENSE](LICENSE).

This project is derived from and inspired by `Ethos_LogView`, which is GPLv3 licensed. See [NOTICE.md](NOTICE.md) for attribution and source details.

Sloppy Log Explorer is not affiliated with, authorized, sponsored, or endorsed by FrSky Electronic Co., Ltd. FrSky and ETHOS are trademarks of their respective owner.
