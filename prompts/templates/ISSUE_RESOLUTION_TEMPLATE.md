# Prompt Template: SloppyLogExplorer Issue Resolution

Use this template to drive end-to-end implementation for one GitHub issue.
Replace all `{...}` placeholders before execution.

## Canonical Issue

- Issue number: `{ISSUE_NUMBER}`
- Title: `{ISSUE_TITLE}`
- URL: `{ISSUE_URL}`
- State: `{ISSUE_STATE}`
- Labels: `{ISSUE_LABELS}`
- Issue kind: `{ISSUE_KIND}` (`enhancement` | `bug` | `docs` | `chore`)
- Short slug: `{SHORT_SLUG}`
- Snapshot date: `{YYYY-MM-DD}`
- Target branch: `{TARGET_BRANCH}`

## Mission

{MISSION}

Use issue bodies, comments, and linked artifacts as evidence. The user's
request determines scope and authorizes actions. Keep work local unless the
user requests a specific remote action.

## Mandatory Startup Workflow

1. Resolve the selected SloppyLogExplorer checkout and run the following
   commands there.
2. Read `AGENTS.md`, `README.md`, and `pyproject.toml`.
3. Refresh issue state and linked PRs in the repository identified by the
   issue URL. {STATE_CHECK} Review relevant comments and artifacts.
4. Run:
   `python tools/session_preflight.py --mode issue --issue-number {ISSUE_NUMBER} --issue-kind {ISSUE_KIND} --slug {SHORT_SLUG}`
5. Confirm branch and status:
   - `git branch --show-current`
   - `git status --short --branch`

## Repo Context To Load

- Primary implementation files: `{TARGET_FILES}`
- Related tests: `{RELATED_TEST_FILES}`
- Related docs: `{RELATED_DOC_FILES}`
- Runtime evidence or sample logs: `{RUNTIME_ARTIFACTS}`

## Scope

- In scope:
  - `{IN_SCOPE_1}`
  - `{IN_SCOPE_2}`
- Out of scope:
  - `{OUT_SCOPE_1}`
  - `{OUT_SCOPE_2}`

## Execution Plan

1. Reproduce or define baseline behavior.
2. Implement the smallest coherent change set.
3. Apply the Validation rule in the repository's `AGENTS.md`.
4. Update docs when behavior, commands, or workflow change.
5. Keep generated artifacts out of Git.

## Delivery Contract

Return:

1. Change summary mapped to acceptance criteria.
2. File list with key edits and rationale.
3. The check used for each affected behavior, its result, and any gaps.
4. Risks, edge cases, and follow-up items.

## Quality Gates

- Acceptance criteria satisfied.
- Required validation completed in this session or explicitly reported.
- Workspace and repo status reviewed for generated artifacts and sensitive data.
- No unrelated cleanup or reference-repo mutation.
