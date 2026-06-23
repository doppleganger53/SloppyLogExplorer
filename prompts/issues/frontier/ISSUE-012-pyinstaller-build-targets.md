# Issue #12 - Add Minimal And Debug PyInstaller Build Targets

Target model: frontier coding model.

Use a frontier model because this issue requires packaging investigation,
dependency tradeoffs, executable validation, and size reporting.

## Canonical Issue

- Issue: #12
- URL: https://github.com/doppleganger53/SloppyLogExplorer/issues/12
- Kind: enhancement
- Target branch: `feature/12-pyinstaller-build-targets`
- Snapshot date: 2026-06-23
- Existing detailed prompt: `prompts/build-size-and-targets-investigation.md`

## Mission

Add explicit `minimal` and `debug` PyInstaller build targets. The minimal target
should reduce end-user package size without breaking Telemetry Plotly rendering,
Qt WebEngine, MapLibre GPS maps, voice-pack behavior, smoke tests, or log
validation. The debug target should remain useful for broader packaging
diagnostics.

## Startup

1. Run `git status --short --branch`.
2. Read `AGENTS.md`, `README.md`, and `pyproject.toml`.
3. Run `python tools/session_preflight.py --mode issue --issue-number 12 --issue-kind enhancement --slug pyinstaller-build-targets`.
4. Read the full existing prompt:
   - `prompts/build-size-and-targets-investigation.md`
5. Inspect these files before editing:
   - `build.py`
   - `build_windows.bat`
   - `README.md`
   - `pyproject.toml`
   - `sloppy_log_explorer/qt_plot.py`
   - `sloppy_log_explorer/gps_map_renderer.py`
   - `sloppy_log_explorer/voice.py`
   - `tests/test_core.py`

## Current Code Facts

- `build.py` supports only `--onefile` and `--clean`.
- `build.py` uses broad `--collect-all plotly`.
- `build.py` collects Kaleido data.
- `build_windows.bat` forwards to `python build.py --clean %*`.
- README does not document minimal or debug target profiles.

## Required Approach

Treat `prompts/build-size-and-targets-investigation.md` as the canonical
investigation plan. Preserve app functionality before optimizing size.

Recommended implementation shape:

- Add `--target {minimal,debug}` to `build.py`, defaulting to the normal
  end-user target after you choose the command contract.
- Keep `--onefile` and `--clean` behavior working.
- Build PyInstaller argument lists from target-specific helpers instead of
  large inline conditional blocks.
- For `debug`, keep broad diagnostics and devtools resources when useful.
- For `minimal`, remove only assets/imports proven unnecessary for runtime.
- Reassess Kaleido by searching for actual runtime use before changing
  dependencies.
- Update `build_windows.bat` and README with exact user-facing commands.

## Size Reporting

For each target you can build, report:

- total `dist\SloppyLogExplorer\_internal` size,
- size of `PyQt6`,
- size of `plotly`,
- size of `numpy.libs`,
- largest 10 files.

Keep generated specs, `build/`, `dist/`, logs, zips, and validation artifacts
out of Git.

## Validation

Run:

```powershell
python -m pytest
python build.py --clean --target minimal
python build.py --clean --target debug
.\dist\SloppyLogExplorer\SloppyLogExplorer.exe --smoke-test
.\dist\SloppyLogExplorer\SloppyLogExplorer.exe --validate-log "<representative-log.csv>"
```

If a build or packaged executable validation cannot run in the environment,
report the exact command and failure mode. If `WinError 5` or access denied
occurs during cleanup, check for a running packaged executable before changing
code.

## Delivery Contract

Return a change summary, target contract, size deltas, touched files,
validation results, residual packaging risks, and a PR body with `Closes #12`.
Do not manually close the issue.
