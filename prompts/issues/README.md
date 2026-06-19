# Issue Prompt Pack

Generated issue-resolution prompts live in this folder.

- Repository: `doppleganger53/SloppyLogExplorer`
- Template: `../templates/ISSUE_RESOLUTION_TEMPLATE.md`
- Generator: `python tools/render_issue_prompt.py --issue-number {N} --issue-kind {enhancement|bug|docs|chore}`

The generator reads GitHub issue metadata with `gh issue view` and writes a
local prompt file named `ISSUE-{number}-{slug}.md`. It does not create, edit,
label, close, or comment on GitHub issues.

Move completed prompt files into `prompts/issues/done/` after the associated PR
is merged.
