# Build Size And Target Profiles Investigation Prompt

You are working in `C:\Users\kurtk\Documents\Workspaces\EthosLua\SloppyLogExplorer`.

Investigate and propose/implement a build cleanup for the Windows PyInstaller packaging. Preserve unrelated local edits. Follow `AGENTS.md`: check `README.md` and `pyproject.toml` before changing packaging or commands, keep generated files out of Git, and run the relevant build validation.

## Current context

- The app currently builds and functions.
- PyInstaller emits this warning during build:

```text
WARNING: Failed to collect submodules for 'plotly.matplotlylib' because importing 'plotly.matplotlylib' raised: ModuleNotFoundError: No module named 'matplotlib'
```

- That warning appears to be harmless because the app uses `plotly.graph_objects`, not `plotly.matplotlylib` or `matplotlib`.
- The `dist\SloppyLogExplorer\_internal` folder was observed at about 607 MB.
- A quick size breakdown showed the largest contributors were approximately:
  - `PyQt6`: 485 MB, mostly Qt WebEngine.
  - `plotly`: 36 MB.
  - `numpy.libs`: 32 MB.
  - `pandas`: 17 MB.
- Large individual files included:
  - `PyQt6\Qt6\bin\Qt6WebEngineCore.dll`: about 193 MB.
  - `PyQt6\Qt6\resources\qtwebengine_devtools_resources.debug.pak`: about 78 MB.
  - `numpy.libs\...\openblas...dll`: about 31 MB.

## Investigation goals

1. Replace broad Plotly collection with the narrowest reliable packaging.
   - Current build uses `--collect-all plotly` in `build.py`, and the generated spec has `collect_all('plotly')`.
   - Determine the minimum Plotly datas/hidden imports needed for telemetry graph HTML generation.
   - Avoid bundling optional Plotly pieces such as `matplotlylib`, Jupyter/labextension assets, widget bundles, or validators if the app does not need them.
   - Confirm whether `include_plotlyjs="cdn"` means local Plotly JS assets can be excluded from the packaged app.

2. Trim Qt WebEngine debug/dev-only resources where safe.
   - Determine whether `qtwebengine_devtools_resources.debug.pak` and other devtools/debug resources can be excluded for an end-user build.
   - Do not remove runtime resources required for normal `QWebEngineView` operation, local MapLibre assets, remote OSM tile loading, or HTML rendering.
   - Validate that the map tab and telemetry graph still load in the packaged executable.

3. Reassess `kaleido` as a runtime/build dependency.
   - Search the app for direct Kaleido usage.
   - If unused, remove the runtime dependency and build collection for the minimal target.
   - Keep it only in a debug/developer target if there is a clear future need for static Plotly export.

4. Add two explicit build targets.
   - `debug` target: may include devtools/debug helpers and broader diagnostics. It should be useful for packaging/debugging WebEngine and Plotly issues.
   - `minimal` target: end-user only. It should exclude unused optional libraries/assets and debug/devtools resources while preserving app functionality.
   - Decide the user-facing command shape, for example `python build.py --target minimal` / `--target debug`, and reflect it in `build_windows.bat` and `README.md`.

## Validation requirements

- Run `python -m pytest`.
- Build both targets, or explain clearly if one target cannot be built in the current environment.
- Validate the packaged executable with:

```powershell
.\dist\SloppyLogExplorer\SloppyLogExplorer.exe --smoke-test
.\dist\SloppyLogExplorer\SloppyLogExplorer.exe --validate-log "path\to\representative-log.csv"
```

- If no representative log is available, use the repo's existing test fixtures/helpers or add a generated temporary validation log outside Git-tracked paths.
- Report size deltas for `_internal`, `PyQt6`, `plotly`, `numpy.libs`, and the largest 10 files for each target.
- Keep `dist/`, `build/`, generated `.spec`, logs, and validation artifacts out of Git.

## Acceptance criteria

- The build warning is either eliminated or documented as harmless for the debug target only.
- The minimal target excludes optional Plotly/Kaleido/devtools content that is not required by runtime behavior.
- The debug target remains available when broader packaging diagnostics are useful.
- README build instructions clearly explain which target normal users should use.
- Tests and packaged smoke/validation checks pass.
