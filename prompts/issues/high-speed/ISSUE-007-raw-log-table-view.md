# Issue #7 - Add A Raw Log Table View

Target model: smaller high-speed coding model.

This prompt deliberately prescribes the table architecture so the issue remains
bounded and does not become a broad performance rewrite.

## Canonical Issue

- Issue: #7
- URL: https://github.com/doppleganger53/SloppyLogExplorer/issues/7
- Kind: enhancement
- Target branch: `feature/7-raw-log-table-view`
- Snapshot date: 2026-06-23

## Mission

Add a Raw Log tab for inspecting the loaded dataframe rows and columns while
preserving source column names, source row order by default, and responsiveness
for wide or long logs.

## Startup

1. Run `git status --short --branch`.
2. Read `AGENTS.md`, `README.md`, and `pyproject.toml`.
3. Run `python tools/session_preflight.py --mode issue --issue-number 7 --issue-kind enhancement --slug raw-log-table-view`.
4. Inspect only these files before editing:
   - `sloppy_log_explorer/main_window.py`
   - `sloppy_log_explorer/models.py`
   - `tests/test_core.py`

## Required Architecture

Do not render the raw log with `QTableWidget` or by inserting every cell with
`setItem(...)`.

Use a `QTableView` backed by a small `QAbstractTableModel` that reads from the
loaded `pandas.DataFrame` on demand:

- `rowCount(...)` returns `len(dataframe)`.
- `columnCount(...)` returns `len(dataframe.columns)`.
- `data(...)` returns a display string only for `Qt.DisplayRole`.
- `headerData(...)` returns source column names for horizontal headers and
  one-based row numbers for vertical headers.
- Default row order must match the source dataframe.

It is acceptable to put the model class in `main_window.py` if it stays small.
Create a separate module only if that is cleaner after inspection.

## UI Requirements

- Add a Raw Log tab, preferably after Telemetry or after Statistics if that tab
  exists on the branch.
- The tab should show a simple empty state when no log is loaded.
- When `MainWindow.load_log(...)` succeeds, set the raw model from
  `self.current_log.dataframe`.
- On load failure or no log, clear the model.
- Enable column resizing and row selection in a way consistent with existing
  tables.
- Sorting is allowed only if implemented through model/proxy mechanics without
  eager cell creation. If sorting looks risky, leave it out and document that it
  is deferred.
- Filtering is optional. Do not add an expensive all-cells filter unless tests
  prove it is bounded.

## Out Of Scope

- Editing raw cells.
- Exporting filtered raw logs.
- Virtualizing custom delegates beyond what Qt's model/view already provides.
- Reworking parser or lazy-loading behavior from issue #8.

## Tests

Add focused tests in `tests/test_core.py`:

- The raw table model reports correct row count, column count, horizontal
  headers, vertical headers, and display data for a small dataframe.
- `MainWindow.load_log(...)` attaches a model for a sample log.
- A synthetic large dataframe, for example 20,000 rows, can be attached by
  model reference without creating `QTableWidgetItem`s or looping over every
  cell.

Prefer direct model tests over fragile rendered UI assertions.

## Validation

Run:

```powershell
python -m pytest
npx --yes pyright --pythonpath .\.venv\Scripts\python.exe sloppy_log_explorer\main_window.py
```

Report any validation command that cannot run, including the exact failure.

## Delivery Contract

Return a change summary, touched files, validation results, and a PR body with
`Closes #7`. Do not manually close the issue.
