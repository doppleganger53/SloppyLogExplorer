# Issue #6 - Add A Selectable Statistics View For Telemetry Channels

Target model: smaller high-speed coding model.

Use this prompt for a direct implementation. Keep the change narrow and avoid
turning this into a dashboard redesign.

## Canonical Issue

- Issue: #6
- URL: https://github.com/doppleganger53/SloppyLogExplorer/issues/6
- Kind: enhancement
- Target branch: `feature/6-selectable-statistics-view`
- Snapshot date: 2026-06-23

## Mission

Add a Statistics tab that summarizes numeric telemetry channels for the loaded
log and lets the user include or exclude channels from that statistics table.

## Startup

1. Run `git status --short --branch`.
2. Read `AGENTS.md`, `README.md`, and `pyproject.toml`.
3. Run `python tools/session_preflight.py --mode issue --issue-number 6 --issue-kind enhancement --slug selectable-statistics-view`.
4. Inspect only these files before editing:
   - `sloppy_log_explorer/main_window.py`
   - `sloppy_log_explorer/analysis.py`
   - `tests/test_core.py`

## Current Code Facts

- `analysis.basic_stats(df, columns)` already returns min, max, mean, and std
  for numeric columns.
- `MainWindow` builds tabs in `_build_ui()` and currently has no statistics
  table or statistics tab.
- `MainWindow.load_log()` refreshes dependent controls immediately after
  `self.current_log` changes.
- Existing table UI in `main_window.py` uses `QTableWidget`, checkable
  `QTableWidgetItem`s, and `_configure_sortable_table(...)`.

## Implementation Requirements

- Add a Statistics tab, preferably immediately after Telemetry.
- The tab must contain a sortable `QTableWidget`.
- Suggested columns:
  - Include
  - Parameter
  - Samples
  - Min
  - Max
  - Mean
  - Std Dev
- Populate it from `self.current_log.parameter_columns`, not from the currently
  plotted columns.
- Add `count` support to the statistics data, either by extending
  `analysis.basic_stats(...)` or by adding a small adjacent helper.
- Use a `set[str]` on `MainWindow` to track statistics-excluded columns for the
  current log.
- On a new log:
  - clear exclusions that no longer exist,
  - repopulate the statistics table,
  - show an empty state if no log is loaded.
- When the Include checkbox changes:
  - update the exclusion set,
  - refresh only the statistics table,
  - do not alter Telemetry plot selections.

## Out Of Scope

- Persisting statistics selections across app restarts.
- Adding charts, exports, or grouped statistics.
- Changing Telemetry plot min/max annotations except for helper reuse.

## Tests

Add focused tests in `tests/test_core.py`:

- `basic_stats(...)` or the new helper reports count, min, max, mean, and std.
- Loading a sample log populates the Statistics tab/table with numeric
  telemetry parameters.
- Unchecking one Include item removes that parameter from visible statistic
  rows without changing `selected_parameter_columns`.
- Loading a second log drops exclusions for missing columns.

Use `QT_QPA_PLATFORM=offscreen` and existing `write_sample(...)` test patterns.

## Validation

Run:

```powershell
python -m pytest
npx --yes pyright --pythonpath .\.venv\Scripts\python.exe sloppy_log_explorer\main_window.py sloppy_log_explorer\analysis.py
```

Report any validation command that cannot run, including the exact failure.

## Delivery Contract

Return a change summary, touched files, validation results, and a PR body with
`Closes #6`. Do not manually close the issue.
