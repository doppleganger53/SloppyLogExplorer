# SloppyLogExplorer issue prompts

No curated implementation packets are active as of 2026-11-14. The renderer
reads issues from the selected checkout's `origin`, or its only remote.
Use `--remote NAME` to select another configured GitHub remote.
For explicitly requested batches, use the [batch template](multi-issue%20prompt.md).

From the selected checkout, run the [renderer](../../tools/render_issue_prompt.py)
with a numeric issue ID in place of `{N}`.

```text
python tools/render_issue_prompt.py --issue-number {N} --dry-run
```

Omit `--dry-run` to write the [implementation template](../templates/ISSUE_RESOLUTION_TEMPLATE.md)
with issue metadata under this checkout's `prompts/issues/`. Use an explicit
`--output-dir` to select a different destination. If a packet already exists,
use a different `--slug` or directory to preserve both copies. Open issues are eligible for
implementation. For a closed issue, use `--historical` for a reference snapshot
under `history/`, or `--regression "user-reported behavior"` for a specific
reassessment. The renderer only reads GitHub.

## Completed packets

GitHub showed these issues closed as completed by the linked merged PRs on
2026-07-05. Move future completed packets to `done/` after verifying the issue
and its merged resolution.

| Archived packet | Merged PR |
| --- | --- |
| [#5 · Telemetry x-axis zoom range](done/ISSUE-005-link-telemetry-x-axis-zoom-range.md) | [#15](https://github.com/doppleganger53/SloppyLogExplorer/pull/15) |
| [#6 · Selectable statistics](done/ISSUE-006-selectable-statistics-view.md) | [#14](https://github.com/doppleganger53/SloppyLogExplorer/pull/14) |
| [#7 · Raw log table](done/ISSUE-007-raw-log-table-view.md) | [#14](https://github.com/doppleganger53/SloppyLogExplorer/pull/14) |
| [#8 · Lazy view loading](done/ISSUE-008-lazy-view-loading-performance.md) | [#15](https://github.com/doppleganger53/SloppyLogExplorer/pull/15) |
| [#9 · Library file-size display](done/ISSUE-009-normalize-library-file-size-display.md) | [#14](https://github.com/doppleganger53/SloppyLogExplorer/pull/14) |
| [#10 · Default telemetry drag mode](done/ISSUE-010-default-telemetry-drag-mode-to-zoom.md) | [#14](https://github.com/doppleganger53/SloppyLogExplorer/pull/14) |
| [#11 · Multi-axis telemetry readability](done/ISSUE-011-multi-axis-telemetry-readability.md) | [#15](https://github.com/doppleganger53/SloppyLogExplorer/pull/15) |
| [#12 · PyInstaller build targets](done/ISSUE-012-pyinstaller-build-targets.md) | [#16](https://github.com/doppleganger53/SloppyLogExplorer/pull/16) |
