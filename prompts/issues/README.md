# Issue Prompt Pack

Generated issue-resolution prompts live in this folder.

- Repository: `doppleganger53/SloppyLogExplorer`
- Template: `../templates/ISSUE_RESOLUTION_TEMPLATE.md`
- Generator: `python tools/render_issue_prompt.py --issue-number {N} --issue-kind {enhancement|bug|docs|chore}`
- Snapshot date: 2026-06-23

The generator reads GitHub issue metadata with `gh issue view` and writes a
local prompt file named `ISSUE-{number}-{slug}.md`. It does not create, edit,
label, close, or comment on GitHub issues.

Curated prompts are split by the model tier that should execute them.

## Smaller High-Speed Coding Model

Use these prompts for localized issues where the implementation path is clear,
the code surface is narrow, and tests can be made deterministic.

| Issue | Prompt | Rationale |
| --- | --- | --- |
| #6 | `high-speed/ISSUE-006-selectable-statistics-view.md` | Mostly a new tab backed by existing `analysis.basic_stats()` plus focused table tests. |
| #7 | `high-speed/ISSUE-007-raw-log-table-view.md` | Prescribed `QAbstractTableModel` approach keeps performance bounded and avoids architecture exploration. |
| #9 | `high-speed/ISSUE-009-normalize-library-file-size-display.md` | Local formatter and library tree display tests only. |
| #10 | `high-speed/ISSUE-010-default-telemetry-drag-mode-to-zoom.md` | Local default-state change across existing telemetry controls and Plotly layout. |

## Frontier Coding Model

Use these prompts where the work needs architectural judgment, live UI or
packaging validation, performance measurement, or cross-language coordination.

| Issue | Prompt | Rationale |
| --- | --- | --- |
| #5 | `frontier/ISSUE-005-link-telemetry-x-axis-zoom-range.md` | Crosses Plotly JS, PyQt WebChannel, GPS payloads, playback state, and live map validation. |
| #8 | `frontier/ISSUE-008-lazy-view-loading-performance.md` | Requires measurement-driven staging across startup restore, log parsing, tab initialization, and regressions. |
| #11 | `frontier/ISSUE-011-multi-axis-telemetry-readability.md` | Needs visual layout judgment and validation beyond simple schema assertions. |
| #12 | `frontier/ISSUE-012-pyinstaller-build-targets.md` | Requires packaging investigation, target design, executable validation, and size reporting. |

Move completed prompt files into `prompts/issues/done/` after the associated PR
is merged.
