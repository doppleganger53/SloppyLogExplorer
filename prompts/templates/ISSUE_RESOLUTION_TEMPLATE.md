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

Implement issue `{ISSUE_NUMBER}` with a root-cause-first approach, scoped to the
issue acceptance criteria and `AGENTS.md`.

## Mandatory Startup Workflow

1. Confirm the working directory is `SloppyLogExplorer`, not parent `EthosLua`
   or a sibling reference checkout.
2. Read `AGENTS.md`, `README.md`, and `pyproject.toml`.
3. Review the issue body/comments and any linked artifacts.
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
3. Add or update tests for new behavior and regressions.
4. Update docs when behavior, commands, or workflow change.
5. Keep generated artifacts out of Git.

## Validation Matrix

Choose the minimum required checks based on touched files:

- Documentation-only changes:
  - review diff and repo status.
- Python behavior changes:
  - `python -m pytest`
- Typing-sensitive pandas/PyQt changes:
  - `npx --yes pyright --pythonpath .\.venv\Scripts\python.exe {TOUCHED_FILES}`
- GPS or Qt WebEngine changes:
  - `python -m pytest`
  - `python tools\validate_gps_map_runtime.py ...`
- Packaging/build changes:
  - documented build command from `README.md` or `build_windows.bat`
  - packaged `--smoke-test` and `--validate-log` when feasible.

If validation times out or hangs, rerun once with a higher timeout. If it still
fails, report the exact command and failure mode.

## Delivery Contract

Return:

1. Change summary mapped to acceptance criteria.
2. File list with key edits and rationale.
3. Validation commands and pass/fail results.
4. Risks, edge cases, and follow-up items.
5. Proposed PR title/body including `Closes #{ISSUE_NUMBER}`.
6. Issue closure sequencing: do not manually close the issue before PR merge.

## Quality Gates

- Acceptance criteria satisfied.
- Required validation completed in this session or explicitly reported.
- Workspace and repo status reviewed for generated artifacts and sensitive data.
- No unrelated cleanup or reference-repo mutation.
