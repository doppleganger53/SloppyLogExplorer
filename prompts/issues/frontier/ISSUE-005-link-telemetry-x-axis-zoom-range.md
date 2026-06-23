# Issue #5 - Link Telemetry X-Axis Zoom Range To Dependent Tabs

Target model: frontier coding model.

Use a frontier model for this issue because it crosses Plotly JavaScript,
PyQt WebChannel signals, `MainWindow` state, GPS map payload generation,
playback timeline math, and live WebEngine validation.

## Canonical Issue

- Issue: #5
- URL: https://github.com/doppleganger53/SloppyLogExplorer/issues/5
- Kind: enhancement
- Target branch: `feature/5-link-telemetry-x-axis-zoom-range`
- Snapshot date: 2026-06-23
- Existing detailed prompt: `prompts/sync-gps-playback-to-telemetry-range.md`

## Mission

Zooming or panning the Telemetry tab must publish the visible x-axis range to
dependent tabs. The Flight Map must render only GPS samples inside that visible
range, and GPS playback/scrubbing must be constrained to the same range.
Resetting the Telemetry view must clear the shared scope.

## Startup

1. Run `git status --short --branch`.
2. Read `AGENTS.md`, `README.md`, and `pyproject.toml`.
3. Run `python tools/session_preflight.py --mode issue --issue-number 5 --issue-kind enhancement --slug link-telemetry-x-axis-zoom-range`.
4. Read the full existing prompt:
   - `prompts/sync-gps-playback-to-telemetry-range.md`
5. Inspect these implementation files before editing:
   - `sloppy_log_explorer/main_window.py`
   - `sloppy_log_explorer/qt_plot.py`
   - `sloppy_log_explorer/plotting.py`
   - `sloppy_log_explorer/gps_map_renderer.py`
   - `sloppy_log_explorer/models.py`
   - `tools/validate_gps_map_runtime.py`
   - `tools/validate_real_log.py`
   - `tests/test_core.py`

## Implementation Direction

Treat `prompts/sync-gps-playback-to-telemetry-range.md` as the canonical
technical plan. Do not re-invent the architecture unless current code has
changed enough to make a named step obsolete.

The core design should be:

- Add a Plotly `plotly_relayout` listener in generated telemetry HTML.
- Emit x-range changes through `_PlotBridge` and `TelemetryPlotWidget`.
- Store canonical visible range in `MainWindow` as elapsed seconds.
- Preserve the telemetry x-range across graph re-renders.
- Pass scope fields into GPS map payload options.
- Filter GPS path/timeline payloads by active scope.
- Keep `MainWindow.selected_index` as the single source of truth for the cursor.
- Keep GPS cursor-only movement using JavaScript cursor updates, not full map
  reloads.

## Risk Areas To Handle Explicitly

- Plotly can emit `xaxis.range[0]`, `xaxis.range[1]`, `xaxis.range`, and
  `xaxis.autorange`.
- Absolute telemetry mode can send date/time strings; relative mode can send
  numbers.
- The UI stores relative time mode as `"relative"`, while `_telemetry_x_values`
  has a `"relative_time"` branch. Handle the current real behavior.
- Playback elapsed time should remain global flight elapsed seconds, not
  seconds since scope start.
- Empty GPS, no GPS in scope, and reset/autorange states must not crash.

## Validation

Run:

```powershell
python -m pytest
npx --yes pyright --pythonpath .\.venv\Scripts\python.exe sloppy_log_explorer\main_window.py sloppy_log_explorer\qt_plot.py sloppy_log_explorer\plotting.py sloppy_log_explorer\gps_map_renderer.py tools\validate_gps_map_runtime.py tools\validate_real_log.py
python tools\validate_gps_map_runtime.py <temp-gps-csv> --color-column "Current(A)" --output validation_artifacts\gps-scope-validation.png
```

If no representative GPS log is available, create a temporary synthetic CSV
outside the repo and state that synthetic data was used.

When feasible, perform desktop validation:

- Launch the app from source.
- Open a GPS-capable log.
- Select telemetry channels.
- Zoom the Telemetry tab to a smaller x-axis range.
- Confirm Flight Map path and playback scope match the visible range.
- Scrub playback and confirm the graph cursor, cursor panel, and map marker
  stay synchronized.
- Reset Telemetry view and confirm the full path returns.

## Delivery Contract

Return a change summary mapped to acceptance criteria, touched files,
validation results, residual risks, and a PR body with `Closes #5`. Do not
manually close the issue.
