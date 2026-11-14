# Issue #9 - Normalize Library File-Size Display To MB

Target model: smaller high-speed coding model.

This is a localized formatter/test change. Do not broaden it into library scan
or sorting work.

## Canonical Issue

- Issue: #9
- URL: https://github.com/doppleganger53/SloppyLogExplorer/issues/9
- Kind: enhancement
- Target branch: `feature/9-normalize-library-file-size-display`
- Snapshot date: 2026-06-23

## Mission

Display all library size values in MB with one decimal point while preserving
raw-byte numeric sort keys.

## Startup

1. Run `git status --short --branch`.
2. Read `AGENTS.md`, `README.md`, and `pyproject.toml`.
3. Run `python tools/session_preflight.py --mode issue --issue-number 9 --issue-kind enhancement --slug normalize-library-file-size-display`.
4. Inspect only these files before editing:
   - `sloppy_log_explorer/main_window.py`
   - `tests/test_core.py`

## Current Code Facts

- `MainWindow._format_size(size)` currently chooses dynamic units: B, KB, MB,
  GB, or TB.
- `populate_library_tree()` uses `_format_size(...)` for both model aggregate
  rows and child log rows.
- `populate_library_tree()` stores raw byte sort keys in
  `Qt.ItemDataRole.UserRole` for the Size column. Keep that behavior.

## Implementation Requirements

- Change `_format_size(size)` to always return MB with one decimal point.
- Use binary megabytes: `size / (1024 * 1024)`.
- Expected examples:
  - `0` -> `0.0 MB`
  - `512` -> `0.0 MB`
  - `1024 * 1024` -> `1.0 MB`
  - `int(2.25 * 1024 * 1024)` -> `2.2 MB`
- Do not change library scanning, grouping, or sort keys.

## Tests

Add or update focused tests in `tests/test_core.py`:

- Direct assertions for `_format_size(...)`.
- Existing `populate_library_tree()` assertions should continue proving both
  parent and child rows use `_format_size(...)`.
- Add one assertion that the Size column `UserRole` value remains the raw byte
  count, not the formatted string.

## Validation

Run:

```powershell
python -m pytest
```

Report any validation command that cannot run, including the exact failure.

## Delivery Contract

Return a change summary, touched files, validation results, and a PR body with
`Closes #9`. Do not manually close the issue.
