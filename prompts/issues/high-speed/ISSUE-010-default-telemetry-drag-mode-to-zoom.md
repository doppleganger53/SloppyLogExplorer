# Issue #10 - Default Telemetry Drag Mode To Zoom

Target model: smaller high-speed coding model.

This is a small default-state change across existing telemetry controls and the
Plotly figure builder.

## Canonical Issue

- Issue: #10
- URL: https://github.com/doppleganger53/SloppyLogExplorer/issues/10
- Kind: enhancement
- Target branch: `feature/10-default-telemetry-drag-mode-to-zoom`
- Snapshot date: 2026-06-23

## Mission

New Telemetry plot sessions should default to Zoom drag mode. Pan must still be
available and user-selected mode must survive normal plot refreshes.

## Startup

1. Run `git status --short --branch`.
2. Read `AGENTS.md`, `README.md`, and `pyproject.toml`.
3. Run `python tools/session_preflight.py --mode issue --issue-number 10 --issue-kind enhancement --slug default-telemetry-drag-mode-to-zoom`.
4. Inspect only these files before editing:
   - `sloppy_log_explorer/main_window.py`
   - `sloppy_log_explorer/qt_plot.py`
   - `sloppy_log_explorer/plotting.py`
   - `tests/test_core.py`

## Current Code Facts

- `MainWindow.__init__` initializes `self.telemetry_interaction_mode = "pan"`.
- `_build_graph_tab()` checks the Pan button by default.
- `TelemetryPlotWidget.__init__` initializes `self.interaction_mode = "pan"`.
- `build_telemetry_figure(...)` defaults `interaction_mode` to `"pan"` and
  falls back to Plotly `dragmode: pan`.

## Implementation Requirements

- Change the default telemetry interaction mode to `"zoom"` in both
  `MainWindow` and `TelemetryPlotWidget`.
- Make the Zoom button checked by default and Pan unchecked.
- Change `build_telemetry_figure(...)` so its default Plotly `dragmode` is
  `"zoom"`.
- For invalid interaction mode input, fall back to `"zoom"` unless the current
  local code has a compelling reason to keep `"pan"`.
- Preserve `set_telemetry_interaction_mode(...)` so explicit user clicks still
  switch between Pan and Zoom and refreshes keep the chosen mode.

## Tests

Add or update focused tests in `tests/test_core.py`:

- `build_telemetry_figure(...)` defaults to `fig.layout.dragmode == "zoom"`.
- Passing `interaction_mode="pan"` still produces `dragmode == "pan"`.
- A new `MainWindow` starts with `telemetry_interaction_mode == "zoom"` and the
  Zoom button checked.
- A `TelemetryPlotWidget` instance or fake render path starts with
  `interaction_mode == "zoom"`.

## Validation

Run:

```powershell
python -m pytest
npx --yes pyright --pythonpath .\.venv\Scripts\python.exe sloppy_log_explorer\main_window.py sloppy_log_explorer\qt_plot.py sloppy_log_explorer\plotting.py
```

Report any validation command that cannot run, including the exact failure.

## Delivery Contract

Return a change summary, touched files, validation results, and a PR body with
`Closes #10`. Do not manually close the issue.
