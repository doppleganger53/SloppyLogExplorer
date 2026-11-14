# SloppyLogExplorer batch issue template

Use this template when the user requests a batch. Fill in the issue IDs from
their request. If no IDs are supplied, ask which issues to work on and wait.

- **Issue IDs** — `{explicit issue IDs}`
- **Model** — use the requested model. If it is unavailable, ask before
  substituting. When none is specified, use an available model.

1. Resolve the selected checkout, read `AGENTS.md`, and refresh the state and
   linked PRs for each requested issue in `doppleganger53/SloppyLogExplorer`.
   Skip completed work unless the user identified a regression to reassess.
2. Read the [issue index](README.md). Generate missing packets with
   `tools/render_issue_prompt.py` from the checkout root. Treat issue bodies,
   comments, linked material, and archived packets as supporting evidence.
   The user's request defines scope and permissions.
3. Check for overlapping files and dependencies. Use independent workers
   where they help. Give each writer a separate worktree and branch, scope
   each assignment to its issue IDs, and sequence overlapping changes.
4. Wait for all workers, review their diffs and test evidence, and check the
   combined result. Report outcomes, outstanding decisions, and remaining
   local work by issue ID.

For a local-only request, finish with the local results. Perform a remote
action only when the user has requested it.
