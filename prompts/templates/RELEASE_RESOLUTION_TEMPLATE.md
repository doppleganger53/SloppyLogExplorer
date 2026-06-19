# Prompt Template: SloppyLogExplorer Release

Use this template for a Windows desktop release. Replace all `{...}`
placeholders before execution.

## Release Target

- Release version: `{VERSION}`
- Version source: `[project].version` in `pyproject.toml`
- Base branch: `main`
- Release branch: `release/v{VERSION}`
- Release tag: `v{VERSION}`
- Release title: `Sloppy Log Explorer v{VERSION}`
- Release notes file: `{RELEASE_NOTES_FILE}`
- Snapshot date: `{YYYY-MM-DD}`

## Mission

Package and publish `Sloppy Log Explorer v{VERSION}` with explicit validation,
auditable release notes, and a Windows onedir archive.

## Mandatory Startup Workflow

1. Confirm the working directory is `SloppyLogExplorer`.
2. Read `AGENTS.md`, `README.md`, `pyproject.toml`, and `CHANGELOG.md`.
3. Check branch/worktree state:
   - `git branch --show-current`
   - `git status --short --branch`
4. Run release preflight:
   - `python tools/session_preflight.py --mode release`
5. Confirm `pyproject.toml` version equals `{VERSION}`.

## Branch And GitHub Gates

1. Ensure current branch is `release/v{VERSION}`.
2. If the worktree is dirty unexpectedly, stop and confirm commit/stash
   strategy.
3. Sync refs:
   - `git fetch origin`
4. Confirm tag does not already exist:
   - `git ls-remote --tags origin v{VERSION}`
5. Confirm GitHub auth:
   - `gh auth status`

## Packaging And Validation

1. Run:
   - `python -m pytest`
2. Build the executable:
   - `.\build_windows.bat`
3. Validate packaged executable when feasible:
   - `.\dist\SloppyLogExplorer\SloppyLogExplorer.exe --smoke-test`
   - `.\dist\SloppyLogExplorer\SloppyLogExplorer.exe --validate-log "{REPRESENTATIVE_LOG}"`
4. Generate release notes:
   - `python tools\write_release_notes.py --version {VERSION} --output {RELEASE_NOTES_FILE}`
5. Create the archive:
   - `python tools\package_release.py --version {VERSION}`

## Publish Steps

1. Commit release metadata changes.
2. Push release branch and open a PR into `main`.
3. Merge the PR, then sync `main`.
4. Tag merged `main`:
   - `git tag v{VERSION}`
   - `git push origin v{VERSION}`
5. Publish GitHub release:
   - `gh release create v{VERSION} dist/SloppyLogExplorer-{VERSION}-windows-x64.zip --title "Sloppy Log Explorer v{VERSION}" --notes-file {RELEASE_NOTES_FILE}`
6. Verify release metadata:
   - `gh release view v{VERSION} --json tagName,name,url,isDraft,isPrerelease,publishedAt`

## Delivery Contract

Return:

1. Exact commit(s) included.
2. Release tag and URL.
3. Release artifact path.
4. Validation commands with pass/fail results.
5. Remaining risks or follow-up items.
