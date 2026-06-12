# Sync GPS Playback Scope With Telemetry X Range Prompt

You are working in `C:\Users\kurtk\Documents\Workspaces\EthosLua\SloppyLogExplorer`.

Implement the next GPS playback enhancement: the Flight Map should render only the GPS flight-path portion that matches the x-axis range currently visible in the Telemetry tab, and the GPS playback timeline should play/scrub only inside that same visible telemetry range.

Do the work end to end. Preserve unrelated local edits. Do not commit unless explicitly asked.

## Start Here

1. Run `git status --short --branch` before editing.
2. Read `AGENTS.md`, `README.md`, and `pyproject.toml`.
3. Inspect these implementation files before changing anything:
   - `sloppy_log_explorer/main_window.py`
   - `sloppy_log_explorer/qt_plot.py`
   - `sloppy_log_explorer/plotting.py`
   - `sloppy_log_explorer/gps_map_renderer.py`
   - `tests/test_core.py`
   - `tools/validate_gps_map_runtime.py`
4. Keep generated files out of Git, including `validation_artifacts/`, `build/`, `dist/`, logs, temp CSVs, and screenshots.

## Current Architecture To Build On

- `TelemetryPlotWidget` renders Plotly HTML through `figure_html(...)`.
- `_PlotBridge` currently emits graph click and arrow-key cursor events with `selectIndex(...)` and `stepIndex(...)`.
- `MainWindow.selected_index` is the canonical cursor row.
- `MainWindow.sync_gps_cursor()` sends the current cursor to `GpsPathWidget.set_cursor(...)`.
- `GpsPathWidget.set_cursor(...)` calls `window.sloppyGpsMap.setCursor(...)` without reloading the map.
- GPS playback state currently lives in `MainWindow`:
  - `gps_playback_playing`
  - `gps_playback_speed`
  - `gps_playback_elapsed_seconds`
  - `gps_timeline_seconds`
  - `gps_playback_tick()`
- Full GPS map reloads should remain limited to log, gradient, or visible telemetry x-range changes. Normal cursor movement must continue to use JavaScript cursor updates only.

## Required Behavior

- When the user zooms or pans the Telemetry tab x-axis, the Flight Map should reload with only the GPS samples inside that visible x-range.
- The GPS playback overlay should treat the visible x-range as its playback scope:
  - The slider left edge is the visible x-range start.
  - The slider right edge is the visible x-range end.
  - Play advances only within that scoped range.
  - Reaching the scoped end pauses playback and snaps to the last telemetry row in scope.
  - Scrubbing seeks to the matching global flight time inside the scope, updates `selected_index`, updates the Telemetry cursor, updates the cursor panel, and moves the map marker.
- If the telemetry x-axis returns to autorange or the reset view command is used, clear the scope and return the Flight Map/playback to the full flight.
- If the selected cursor row is outside a newly visible scope, clamp it to the nearest in-scope telemetry row and refresh the graph cursor and cursor panel.
- If a visible range contains no GPS samples, show the existing no-data style map message without errors and disable or hide playback controls.
- If no log is loaded, no telemetry columns are selected, or the graph is empty, do not force a GPS scope.
- The behavior must work in both telemetry time modes:
  - `Absolute`: Plotly relayout ranges may arrive as date/time strings.
  - `Relative`: Plotly relayout ranges should be treated as elapsed seconds.

Important existing naming trap: the UI stores `"relative"` in `MainWindow.telemetry_time_mode`, while `_telemetry_x_values(...)` only has a special `"relative_time"` branch and otherwise falls back to elapsed seconds. Handle the current real behavior instead of assuming the branch name is correct.

## Recommended Implementation Plan

### 1. Emit Telemetry X-Range Changes From Plotly

Extend `_PlotBridge` in `sloppy_log_explorer/qt_plot.py` with a signal and slot for visible x-range changes. Use object-friendly types because Plotly may send numbers, strings, or `null`.

Suggested shape:

```python
x_range_changed = pyqtSignal(object, object)

@pyqtSlot(object, object)
def setXRange(self, start: object, end: object) -> None:
    self.x_range_changed.emit(start, end)
```

Expose a matching `TelemetryPlotWidget.x_range_changed` signal and connect the bridge signal to it.

In `figure_html(...)`, listen to `plotly_relayout` on the Plotly div:

- For zoom/pan, read either:
  - `event["xaxis.range[0]"]` and `event["xaxis.range[1]"]`
  - or `event["xaxis.range"]` when Plotly emits an array.
- For reset/autorange, detect `event["xaxis.autorange"] === true` and send `null, null`.
- Debounce the bridge call lightly, for example 50-150 ms, so a drag does not reload the map dozens of times.
- Add tests that assert the generated HTML contains `plotly_relayout`, `xaxis.range[0]`, `xaxis.autorange`, and `setXRange`.

### 2. Store A Canonical Visible Range In MainWindow

Add a `MainWindow.telemetry_visible_elapsed_range: tuple[float, float] | None` state value. Store the scope in global elapsed seconds, not row numbers and not raw Plotly x values.

Add a slot such as `set_telemetry_visible_x_range(self, start: object, end: object) -> None`, connected to `self.graph_view.x_range_changed`.

This method should:

- Convert Plotly values to elapsed seconds for the current log and time mode.
- Normalize reversed ranges by sorting start/end.
- Clamp the values to the flight duration from `relative_seconds(self.current_log)`.
- Clear the scope when either value is `None` or cannot be parsed.
- Avoid unnecessary GPS reloads if the normalized scope did not change meaningfully.
- Clamp `selected_index` into the new scope when needed.
- Refresh the graph so the cursor line stays visible while preserving the current x-range.
- Refresh the GPS map so the rendered path changes to the visible scope.
- Sync the GPS cursor after refresh.

Conversion guidance:

- If the Plotly value is numeric, treat it as elapsed seconds.
- If it is a date/time string and `self.current_log.time` has usable timestamps, parse with `pd.to_datetime(...)` and subtract the first valid log timestamp to get elapsed seconds.
- If parsing fails, clear the scope rather than crashing.

Add helpers instead of stuffing this into one large method. Good helper names:

- `_telemetry_axis_value_to_elapsed(...)`
- `_normalise_telemetry_elapsed_range(...)`
- `_gps_playback_scope_bounds()`
- `_clamp_index_to_elapsed_scope(...)`

### 3. Preserve The Telemetry Graph Range Across Re-Renders

This is required. The graph is rebuilt whenever `set_selected_index(...)` calls `refresh_graph()`. If you do not preserve the x-range, cursor movement and playback will immediately reset the graph and therefore reset the map scope.

Update `build_telemetry_figure(...)` to accept an optional x-axis range. Store the range as elapsed seconds in `MainWindow`, then convert it back to the axis value type used by the current graph:

- For relative mode, pass `[start_seconds, end_seconds]`.
- For absolute mode with real timestamps, pass `[start_timestamp, end_timestamp]`.

Pass that range from `MainWindow.refresh_graph(...)` into `TelemetryPlotWidget.set_plot(...)`, then into `build_telemetry_figure(...)`.

`reset_telemetry_view()` should clear `telemetry_visible_elapsed_range`, re-render the graph without an x-axis range, refresh the GPS map, and sync the cursor.

### 4. Scope The GPS Payload And Timeline

Extend `GpsGradientOptions` in `sloppy_log_explorer/models.py` with optional scope fields, for example:

```python
scope_start_seconds: float | None = None
scope_end_seconds: float | None = None
```

Pass those fields from `MainWindow._gps_gradient_options()`.

In `build_gps_map_payload(...)`:

- Continue using `relative_seconds(log)` as the source of truth for elapsed time.
- Normalize and clamp the optional scope against the full log duration.
- Filter GPS candidates to the scope before building `points`, `pathParts`, and `segments`.
- Update the `timeline` payload to include:
  - `enabled`
  - `durationSeconds` as scoped duration (`scope_end - scope_start`)
  - `startElapsedSeconds`
  - `endElapsedSeconds`
  - `rows`
- Keep the existing full-flight behavior unchanged when no scope is active.
- If filtering removes all GPS points, return a useful no-GPS-in-range message and a disabled timeline.

Keep gradient coloring behavior consistent with the currently rendered scoped path.

### 5. Update Playback Bounds Without Breaking Global Cursor Time

Keep `gps_playback_elapsed_seconds` as global flight elapsed time. Do not convert it to "seconds since scope start"; that will break `nearest_index(...)`, telemetry cursor sync, and marker interpolation.

Instead:

- Add helpers in `MainWindow` to return active scope start/end in global elapsed seconds.
- Clamp `seek_gps_elapsed(...)` to active scope bounds.
- Make `set_gps_playback_playing(True)` start from the current selected row if it is inside scope, otherwise from the scope start.
- Make `gps_playback_tick()` stop at the active scope end, not necessarily the full log end.
- Keep `nearest_index(self.current_log, elapsed)` as the way to map elapsed time back to `selected_index`.

Update `_gps_cursor_payload()` to include any new scope fields the JavaScript needs, for example:

```python
"scopeStartSeconds": scope_start,
"scopeEndSeconds": scope_end,
"durationSeconds": scope_end - scope_start,
```

### 6. Update The Map Renderer Slider Math

In `sloppy_log_explorer/gps_map_renderer.py`, update the playback JavaScript so the overlay maps slider positions to the active timeline scope:

- Read `timeline.startElapsedSeconds` and `timeline.endElapsedSeconds`.
- Clamp cursor elapsed time to `[scopeStart, scopeEnd]`.
- Slider value should be based on `(elapsedSeconds - scopeStart) / (scopeEnd - scopeStart)`.
- Slider input should call `gpsBridge.seekElapsed(scopeStart + ratio * (scopeEnd - scopeStart))`.
- Keep the marker interpolation in global elapsed seconds because the path points contain global `elapsedSeconds`.
- Keep `window.sloppyGpsMap.setCursor(...)`, `setPlayback(...)`, and `getState()` working.
- Extend debug state so validation can assert the active scope start/end and selected cursor time.

### 7. Tests To Add Or Update

Add focused tests in `tests/test_core.py`:

- `figure_html(...)` contains the Plotly relayout bridge tokens for x-range and autorange reset.
- `TelemetryPlotWidget` bridge exposes an x-range signal and `setXRange(...)` slot.
- `build_telemetry_figure(...)` applies a passed x-axis range.
- `build_gps_map_payload(...)` with a scope such as `2.0` to `5.0` returns only scoped GPS points/segments and a scoped timeline.
- `MainWindow` converts relative x-range values to an elapsed scope, refreshes GPS once, and preserves the graph range on cursor changes.
- `MainWindow` converts absolute timestamp x-range values to the same elapsed scope.
- Cursor selection outside a new scope clamps to the nearest in-scope row and syncs the GPS cursor.
- Playback tick stops at the scoped end and pauses.
- Clearing autorange removes the scope and restores the full GPS timeline.
- Cursor movement inside an existing scope must still update the map through `GpsPathWidget.set_cursor(...)` without a full GPS map reload.

Extend `tools/validate_gps_map_runtime.py` to exercise a scoped timeline and assert that `debugMapState()` reports the scoped start/end and selected cursor time.

Extend `tools/validate_real_log.py` token checks for any new public JavaScript API or timeline fields you add.

## Validation Requirements

Run:

```powershell
python -m pytest
npx --yes pyright --pythonpath .\.venv\Scripts\python.exe sloppy_log_explorer\main_window.py sloppy_log_explorer\qt_plot.py sloppy_log_explorer\plotting.py sloppy_log_explorer\gps_map_renderer.py tools\validate_gps_map_runtime.py
```

Also run the live WebEngine validator with a temporary GPS CSV outside Git-tracked paths:

```powershell
python tools\validate_gps_map_runtime.py <temp-gps-csv> --color-column "Current(A)" --output validation_artifacts\gps-scope-validation.png
```

Use Computer Use or an equivalent desktop verification path if available:

- Launch the app from source.
- Open a GPS-capable sample log.
- Select telemetry columns.
- Zoom the Telemetry tab to a smaller x-axis range.
- Open Flight Map.
- Confirm only the scoped path is rendered.
- Confirm playback starts at the scoped left edge and pauses at the scoped right edge.
- Scrub the playback timeline and verify the graph cursor, cursor panel, map marker, and marker readout stay synchronized.
- Reset the Telemetry view and confirm the full path and full playback timeline return.

If no real log is available, create a temporary synthetic CSV in `%TEMP%`. State clearly that synthetic data was used.

## Acceptance Criteria

- Telemetry x-axis zoom/pan controls the Flight Map rendered path and playback scope.
- Reset/autorange restores full-flight map and playback.
- Existing GPS gradient controls and marker telemetry selectors still work.
- Cursor sync remains single-source-of-truth through `MainWindow.selected_index`.
- Cursor-only changes do not reload the GPS map.
- Empty/no-GPS/no-in-scope-GPS states do not crash.
- Tests, Pyright, and WebEngine validation pass.
- No generated artifacts are staged.
