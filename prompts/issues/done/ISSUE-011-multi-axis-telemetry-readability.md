# Issue #11 - Fix Crowded Labels And Tick Marks In Multi-Axis Telemetry Plots

Target model: frontier coding model.

Use a frontier model because this issue requires visual layout judgment,
schema-safe Plotly changes, and validation beyond the existing unit tests.

## Canonical Issue

- Issue: #11
- URL: https://github.com/doppleganger53/SloppyLogExplorer/issues/11
- Kind: bug
- Target branch: `fix/11-multi-axis-telemetry-readability`
- Snapshot date: 2026-06-23

## Mission

Make multi-axis Telemetry plots readable when several telemetry channels are
selected. Right-side axis titles and tick labels should not overlap each other
or the plot content, while preserving the ability to compare channels with
different scales.

## Startup

1. Run `git status --short --branch`.
2. Read `AGENTS.md`, `README.md`, and `pyproject.toml`.
3. Run `python tools/session_preflight.py --mode issue --issue-number 11 --issue-kind bug --slug multi-axis-telemetry-readability`.
4. Inspect these files before editing:
   - `sloppy_log_explorer/plotting.py`
   - `sloppy_log_explorer/qt_plot.py`
   - `sloppy_log_explorer/main_window.py`
   - `tests/test_core.py`

## Current Code Facts

- `build_telemetry_figure(...)` gives each selected channel its own y-axis.
- Only the first six y-axes show tick labels.
- Right-side secondary axes use `anchor="free"`, `overlaying="y"`,
  `side="right"`, and positions derived from `0.86 + idx * 0.045`.
- `test_many_selected_telemetry_columns_keep_plot_readable` currently asserts
  fixed margin and visible-axis behavior, but does not verify actual overlap.

## Investigation Requirements

Generate or use a representative multi-channel log and inspect the rendered
Telemetry plot. The ignored local screenshot mentioned in the issue may not be
available, so create a reproducible local sample if needed.

Evaluate at least these options before choosing:

- Increase spacing and right margin for visible right axes.
- Reduce the number of simultaneously visible right-side axes.
- Use shorter axis titles, standoff, or title/tick label placement changes.
- Move less important scale details into hover/legend while keeping enough
  axes visible for scale comparison.

Do not solve this by hiding all secondary axes unless you document why that is
the best tradeoff.

## Implementation Guardrails

- Preserve one scale per selected plotted channel where practical.
- Keep downsampling and trace limits intact.
- Keep the first 24 selected trace limit and hidden selection annotation.
- Keep dark and light theme readability.
- Avoid brittle pixel-perfect tests. Prefer schema assertions plus a generated
  visual validation artifact when feasible.

## Tests

Update or add focused tests in `tests/test_core.py`:

- Multi-channel figure layout uses the new axis spacing/margin/visibility
  contract.
- Hidden axes still do not show ticks or titles.
- Legend and hidden-trace annotation behavior remains intact.
- The Plotly layout remains serializable.

If you add helper functions for margin or axis placement, test them directly.

## Validation

Run:

```powershell
python -m pytest
npx --yes pyright --pythonpath .\.venv\Scripts\python.exe sloppy_log_explorer\plotting.py sloppy_log_explorer\qt_plot.py sloppy_log_explorer\main_window.py
```

When feasible, visually validate the Telemetry tab with a representative
multi-axis log and include where the screenshot or artifact was saved. Keep
artifacts under `validation_artifacts/` and out of Git.

## Delivery Contract

Return a change summary, chosen layout rationale, touched files, validation
results, residual visual risks, and a PR body with `Closes #11`. Do not
manually close the issue.
