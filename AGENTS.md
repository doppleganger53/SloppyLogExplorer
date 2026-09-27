# AGENTS.md

Repo-local guidance for `SloppyLogExplorer`.

## Priority And Scope

- Treat `SloppyLogExplorer/` as the active implementation repo.
- If launched from the parent `EthosLua` workspace, keep the parent workspace as
  orchestration-only. Resolve the selected SloppyLogExplorer checkout's absolute
  root, then run tools there or scope Git commands with `git -C "<repo-root>"`.
- Treat sibling repositories, including `sloppy-ethos/`, as read-only reference
  evidence unless the user explicitly targets them.
- Keep changes scoped to the user request. Avoid broad refactors and unrelated
  cleanup.
- Preserve unrelated local edits and do not overwrite user work.

## Startup Workflow

1. Check the active Git scope before editing, using resolved absolute roots:
   - this checkout: `git -C "<repo-root>" status --short --branch`
   - when using the parent workspace: `git -C "<workspace-root>" status --short --branch`
   Do not infer a checkout's location from its folder name; review worktrees may
   be elsewhere.
2. Read this file before applying repository policy.
3. When changing behavior, packaging, or commands, check `README.md` and
   `pyproject.toml` first.
4. When acting on a GitHub issue, review the issue body/comments and any prompt
   under `prompts/issues/`.
5. Before copying behavior, code, assets, or docs from a reference project,
   verify license and attribution requirements.

## Issue And Branch Workflow

- Issue-linked work is any session tied to a GitHub issue number/URL or a prompt
  under `prompts/issues/`.
- Issue-linked work must not mutate repository files while on `main`.
- Before issue-linked edits, run:
  `python tools/session_preflight.py --mode issue --issue-number {N} --issue-kind {enhancement|bug|docs|chore} --slug {short-slug}`
- Branch naming conventions:
  - `feature/{issue-number}-{short-slug}` for enhancements
  - `fix/{issue-number}-{short-slug}` for bugs
  - `docs/{issue-number}-{short-slug}` for docs/process work
  - `chore/{issue-number}-{short-slug}` for maintenance/tooling work
- Non-issue work may be performed on `main`, but confirm before mutating files
  on `main`.
- PRs should target `main` and include linked-closing keywords such as
  `Closes #123`.
- Do not manually close linked issues before merge; rely on merged PR closing
  keywords.

## Release Workflow

- The package version source of truth is `[project].version` in `pyproject.toml`.
- Release-prep branches use `release/v{version}`.
- Before release actions, run:
  `python tools/session_preflight.py --mode release`
- Keep release notes in `CHANGELOG.md`.
- Generate GitHub release bodies with:
  `python tools/write_release_notes.py --version {version} --output validation_artifacts/release-notes-{version}.md`
- Build the minimal Windows onefile output with the documented build command,
  then package it with:
  `python tools/package_release.py`
- The release archive includes the executable plus `README.md`, `NOTICE.md`,
  `LICENSE`, and `CHANGELOG.md`.
- Publish releases with `gh release create ... --notes-file`, not long inline
  notes.

## Working Rules

- Prefer root-cause fixes over compatibility shims unless a real compatibility
  requirement is confirmed.
- Prefer efficient, high-performance code over low-probability fallback chains.
- Diagnose stale local state before changing code when failures look
  environment-driven.
- Keep generated and machine-local files out of Git, including `.venv/`,
  `build/`, `dist/`, `validation_artifacts/`, `*.log`, `*.zip`, `*.wav`, and
  `*.egg-info/`.
- Update `NOTICE.md` when attribution changes.
- No destructive Git commands unless the user explicitly requests them.

## Validation Matrix

- Documentation-only changes:
  - review the diff and repo status.
- Python behavior changes:
  - `python -m pytest`
- Typing-sensitive pandas/PyQt changes:
  - `npx --yes pyright --pythonpath .\.venv\Scripts\python.exe {touched-files}`
- GPS or Qt WebEngine behavior changes:
  - `python -m pytest`
  - run `python tools\validate_gps_map_runtime.py ...` or
    `python tools\validate_real_log.py ...` when the change affects live map or
    real-log behavior.
- Packaging/build changes:
  - run the relevant command from `README.md` or `build_windows.bat`.
  - when feasible, validate the packaged executable with `--smoke-test` and
    `--validate-log`.
- Broad or cross-cutting changes:
  - `python -m pytest`

If a required validation command times out or hangs, rerun once with a higher
timeout. If it still fails, report the exact command and failure mode; do not
claim validation passed.

## Definition Of Done

- Required validation for touched files completed or explicitly reported as not
  run.
- Parent workspace and repository status reviewed before finalizing.
- Generated artifacts and ignored local outputs are not staged.
- Any security or privacy concern, including logs, local paths, API keys, or
  sensitive telemetry, is called out.
