# Issue #8 - Improve Startup And Log-Load Performance With Lazy View Loading

Target model: frontier coding model.

Use a frontier model because this issue needs performance measurement and
staged architectural changes across startup restore, log parsing, tab
initialization, and UI refresh behavior.

## Canonical Issue

- Issue: #8
- URL: https://github.com/doppleganger53/SloppyLogExplorer/issues/8
- Kind: enhancement
- Target branch: `feature/8-lazy-view-loading-performance`
- Snapshot date: 2026-06-23

## Mission

Keep startup and log loading responsive by deferring expensive work until the
relevant tab or workflow needs it, while preserving current behavior once each
view is opened.

## Startup

1. Run `git status --short --branch`.
2. Read `AGENTS.md`, `README.md`, and `pyproject.toml`.
3. Run `python tools/session_preflight.py --mode issue --issue-number 8 --issue-kind enhancement --slug lazy-view-loading-performance`.
4. Inspect these files before editing:
   - `sloppy_log_explorer/main_window.py`
   - `sloppy_log_explorer/parser.py`
   - `sloppy_log_explorer/library.py`
   - `sloppy_log_explorer/qt_plot.py`
   - `sloppy_log_explorer/gps_map_renderer.py`
   - `tests/test_core.py`
   - `tools/validate_real_log.py`

## Current Code Facts

- `_restore_state()` loads the saved library and last log during
  `MainWindow` construction.
- `parser.load_log()` reads and normalizes the full CSV into a dataframe,
  detects time, numeric columns, and GPS columns, then returns a full
  `LoadedLog`.
- `MainWindow.load_log()` immediately initializes parameter selection, GPS
  color/value combos, analysis combos, flight notes, Telemetry plot, GPS map,
  and cursor panel.
- `scan_library()` should remain metadata-only.

## Required Approach

Do not start with a rewrite. Start with measurement and implement one or two
staged deferrals that have clear benefit and low regression risk.

Recommended phases:

1. Add lightweight timing instrumentation for local investigation only, or use
   a small helper script under `tools/` if it is useful and testable.
2. Measure representative startup with saved state and log-load paths before
   changing behavior.
3. Defer non-visible tab work first:
   - do not render GPS map until Flight Map is opened,
   - do not populate heavy secondary tab data until its tab is opened,
   - keep Telemetry and cursor panel correct for the initial visible workflow.
4. Consider delaying last-log restore until after the main window is shown only
   if it improves user-perceived startup and tests can cover the behavior.
5. Add explicit dirty/initialized flags for deferred views instead of relying
   on implicit widget state.

## Guardrails

- Do not break `MainWindow.selected_index` cursor synchronization.
- Do not regress library scanning into content reads.
- Do not defer parser work in a way that produces partially initialized
  `LoadedLog` objects unless you design a real staged data contract.
- Do not hide exceptions caused by deferred refreshes. Surface them through the
  existing warning/error patterns.
- Keep generated timing outputs and validation artifacts out of Git.

## Tests

Add focused regression tests in `tests/test_core.py`:

- Loading a log marks deferred views dirty but does not eagerly refresh GPS map
  or other expensive non-visible tab work.
- Opening the relevant tab performs the deferred refresh once.
- Reopening the same tab does not refresh again unless state changed.
- Column selection and cursor updates still work before deferred tabs open.
- Existing metadata-only `scan_library()` behavior remains covered.

Use monkeypatches or fakes to count method calls rather than timing tests.

## Validation

Run:

```powershell
python -m pytest
npx --yes pyright --pythonpath .\.venv\Scripts\python.exe sloppy_log_explorer\main_window.py sloppy_log_explorer\parser.py sloppy_log_explorer\library.py
python tools\validate_real_log.py <representative-large-log.csv>
```

If no representative large log is available, create a temporary synthetic log
outside the repo and report that limitation. Include before/after timing notes
for the paths you changed.

## Delivery Contract

Return a change summary, touched files, before/after performance evidence,
validation results, residual risks, and a PR body with `Closes #8`. Do not
manually close the issue.
