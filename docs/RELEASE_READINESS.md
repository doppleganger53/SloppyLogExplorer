# Sloppy Log Explorer 0.2.0 release verification

Verification date: 2026-09-27. Target: Windows x64, Python 3.12, Qt/Qt WebEngine 6.11.2.

## Scope

Completed the imagery/HeatMap work from issues #31, #33, and #34, fixed session
grouping persistence from #36, and reviewed the application,
data handling, generated HTML, bundled libraries, packaging, and desktop UX.
The fixes are summarized in `CHANGELOG.md`; regressions are covered by the test
suite and the live validation tools.

## Verification results

Local machine-readable evidence and screenshots are stored under the ignored
`validation_artifacts/release-readiness/` and
`validation_artifacts/public-release-maps/` folders.

- Full regression suite: **314 passed, 1 skipped** in 17.06 seconds. The skip
  requires Windows symlink-creation privileges; see the boundary below.
- Minimal Windows onefile build: **passed**. The 207,305,372-byte executable
  passed `--smoke-test`, `--validate-log`, and `--validate-ui-log` with isolated
  settings and a GPS fixture whose filename contains a space. Its bundled
  metadata reports 0.2.0; the three MapLibre modules, CSS, and license are
  present, and incompatible ICU/devtools files are absent.
- Release ZIP: **passed** CRC and content verification. It contains only the
  validated executable, README, NOTICE, LICENSE, and CHANGELOG. The embedded
  executable's SHA-256 matches the tested file.
- Real FrSky log: 6,251 rows, comparison flight, battery analysis, 893-file
  library scan, and native Qt telemetry graph passed.
- UX: all 10 tabs, 45 screenshots, dark/light themes, exact 1500x940 and
  1280x800 client sizes. Checks include comparison, cursor movement, notes,
  batteries, sync, HeatMap generation, normalization, and unclipped graph
  legends/axes. Validation uses temporary settings and synthetic flights.
- Speech: real Windows SAPI synthesis produced a valid mono, 16-bit, 22,050 Hz
  WAV; 55,767 audio frames verified without playback.
- Performance: cached comparison lookup averaged 0.018 ms per cursor update
  over 1,000 updates with two 500,000-row logs. Vectorized elapsed-time
  calculation took approximately 11 ms for 500,000 rows. These are local
  microbenchmarks, not guarantees for all machines or storage devices.
- Full application and validation-tool Pyright: zero errors and warnings.
- Live Flight Map and HeatMap: NAIP imagery, global GIBS coverage, simulated
  NAIP/GIBS failure with OSM recovery, retry, opacity, camera preservation,
  overlays, and attribution passed. Tests verify rendered pixel differences;
  they do not infer successful imagery from configured layer names alone.
  Both live attribution-injection checks removed the malicious attributes.
- Flight Map footer controls remain separate at 1280x820 and 480x360.
- Packaged Qt diagnosis: an inherited tool directory supplied an incompatible
  ICU DLL during bundling. Identical small builds reproduced the startup error
  with the inherited PATH and passed with only Python/Windows search paths.
  The build now isolates its search path and restores the caller environment.

## Security and privacy

- Escaped embedded map data and made template expansion non-recursive to stop
  crafted telemetry text from becoming executable script.
- Updated the standalone MapLibre runtime to 6.4.1, with official archive
  integrity verified and its license retained. This addresses
  [CVE-2026-85061](https://github.com/maplibre/maplibre-gl-js/security/advisories/GHSA-jrc7-96c5-q579).
- Required Plotly 7.1 or newer, which also updates its embedded MapLibre code;
  see the [upstream release notes](https://github.com/plotly/plotly.py/releases/tag/v7.1.0).
  Disabled cloud chart sharing explicitly and checked its absence in the live
  telemetry modebar.
- Required Qt runtime 6.11.2 or newer, covering the verified fixes for
  [CVE-2026-76151](https://www.qt.io/blog/security-advisory-cve-2026-76151)
  and [CVE-2026-19248](https://www.qt.io/blog/security-advisory-cve-2026-19248).
- The installed Python dependency audit reported no known advisories. Bandit
  found one reviewed false positive: a SQL fragment chosen only from internal
  constants; user-provided values use SQLite bound parameters. Neither scan is
  a proof that all possible vulnerabilities are absent.
- Release dependency collection is isolated from unrelated tools on PATH, so
  their DLLs cannot silently become dependencies of the shipped application.
- Sync and voice exports reject unsafe paths/names and stage output before
  replacing existing files. Flight-note save failures preserve the draft.
- Real logs, local settings, private paths in diagnostics, and generated media
  remain outside Git and the release archive. Map tiles still disclose the
  viewed area and network address to the documented public tile providers.

## Reproduction

Use the project virtual environment for these commands:

```powershell
python -m pytest
npx --yes pyright --pythonpath .\.venv\Scripts\python.exe sloppy_log_explorer build.py tools/package_release.py tools/validate_real_log.py tools/validate_gps_map_runtime.py tools/validate_reception_map_runtime.py tools/validate_release_ux.py
python tools/validate_release_ux.py
python tools/validate_real_log.py "{LOG_PATH}" --compare "{COMPARE_PATH}" --library-root "{LIBRARY_ROOT}" --state-root validation_artifacts/real-log-state --ui-platform native --render-graph validation_artifacts/real-log.png
python tools/validate_gps_map_runtime.py "{GPS_LOG_PATH}" --state-root validation_artifacts/gps-state --output validation_artifacts/gps-map.png
python tools/validate_reception_map_runtime.py --state-root validation_artifacts/heatmap-state --output validation_artifacts/heatmap.png
python tools/session_preflight.py --mode release
.\build_windows.bat --target minimal
.\dist\SloppyLogExplorer.exe --smoke-test
.\dist\SloppyLogExplorer.exe --validate-log "{LOG_PATH}"
python tools/package_release.py
```

Run smoke/UI validations with a temporary `APPDATA` directory to isolate saved
settings. Live imagery checks require internet access; they verify actual
rendered tiles as well as simulated provider failures. The HeatMap validator
generates its own synthetic data. Early NAIP requests intermittently failed;
the application fell back to GIBS, and final strict checks passed on both maps.

## Verification boundaries

The Windows symlink-escape regression is skipped when the host lacks symlink
creation privileges; ordinary path containment, overlapping directories,
stale candidates, and interrupted-copy preservation are tested. Cross-platform
desktop behavior and every display scaling/GPU combination were not tested.
These checks cover the unsigned Windows build. Publication and release tags
are recorded separately on the repository's GitHub Releases page.

The onefile executable must unpack its runtime at startup. Packaged validation
startup varied from about 9 to 37 seconds in this run; the first check ran
alongside another extraction. The documented onedir development build trades
portability for faster startup. No universal startup-time claim is made.

## Local release artifacts

- Archive: `dist/SloppyLogExplorer-0.2.0-windows-x64.zip` (206,001,725 bytes).
- Archive SHA-256: `cc83abac66456c330e84d23a6bab572fd36a1cd2d2840fc79a5b3241b18cb46d`.
- Executable SHA-256: `ef1ce74da527cf0830d9055f3ae8a3c6dcfe0fd56009bb3e0aca3182e8b1a30f`.
- Prepared release notes: `validation_artifacts/release-notes-0.2.0.md`.
- Release changes were organized into focused commits on `release/v0.2.0`.
  Final integration also preserves the local checkout-routing and release
  template guidance. The combined tree passed a fresh full regression run
  (314 passed, 1 skipped) and Pyright (zero errors or warnings).
- The retired Spark branch was reviewed against the current implementation:
  elapsed-range bridging, GPS scope filtering, cursor clamping, scoped playback,
  and validation coverage are already implemented by newer changes. The current
  code additionally handles non-monotonic timelines and stale WebChannel
  documents, so the older implementation was not imported.
